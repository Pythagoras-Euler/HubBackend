"""Independent, bounded TruckersHub reconciliation with restart-safe receipts."""
import asyncio
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from fastapi import Request
from functions.general import genrid
from functions.dataop import compress, decompress
from functions.truckershub_routes import get_key, api_get
from functions.tracker_sources import save_source
from truckershub_import import convert_job, active_job, months_since, job_status
from truckershub_routes import route_points, encode_route
from logger import logger


async def attach_route(app, rid, key, sid, lid):
    if 'route' not in app.config.plugins:
        return
    await app.db.execute(rid,'SELECT d.data FROM dlog d LEFT JOIN telemetry t ON t.logid=d.logid WHERE d.logid=%s AND t.logid IS NULL',(lid,))
    row=await app.db.fetchone(rid)
    if not row:
        return
    points=route_points(await api_get(app,rid,key,f'routes/{sid}'))
    if len(points)<2:
        return False
    game=json.loads(decompress(row[0]))['data']['object']['game']['short_name']
    await app.db.execute(rid,'SELECT logid FROM dlog WHERE logid=%s FOR UPDATE',(lid,))
    if not await app.db.fetchone(rid):
        await app.db.commit(rid)
        return
    await app.db.execute(rid,'SELECT logid FROM telemetry WHERE logid=%s',(lid,))
    if not await app.db.fetchone(rid):
        await app.db.execute(rid,'INSERT INTO telemetry(logid,uuid,userid,data) SELECT logid,%s,userid,%s FROM dlog WHERE logid=%s',
                             (f'truckershub:{sid}',compress(encode_route(game,points)),lid))
    await app.db.commit(rid)
    return True


async def import_job(request, raw):
    from functions.tracker import handle_new_job
    converted=convert_job(raw)
    return await handle_new_job(request,raw,converted,'truckershub',bypass_tracker_check=True,
                                allow_external_driver=True,historical=True)


async def month_jobs(app, rid, key, year, month):
    page = 1
    while True:
        suffix = f'&page={page}' if page > 1 else ''
        raw = await api_get(app, rid, key, f'jobs?month={month}&year={year}{suffix}', include_links=True)
        jobs = raw.get('data') if isinstance(raw, dict) else raw
        if not isinstance(jobs, list):
            raise ValueError('Unexpected jobs response')
        for job in jobs:
            yield job
        next_link = (raw.get('links') or {}).get('next') if isinstance(raw, dict) else None
        if not next_link:
            return
        # Only use the page number; never forward credentials to a provider-supplied URL.
        next_page = int(parse_qs(urlparse(next_link).query).get('page', ['0'])[0])
        if next_page <= page:
            raise ValueError('Invalid jobs pagination')
        page = next_page


async def reconcile(request):
    app,rid=request.app,request.state.dhrid
    key=await get_key(app,rid)
    if not key:
        return
    lock=app.redis.lock('truckershub-import-lock',timeout=300,blocking=False)
    if not lock.acquire(blocking=False):
        return
    state='truckershub-import-status'
    counts={'scanned':0,'imported':0,'linked':0,'failed':0,'review':0}
    started=time.monotonic(); now=int(time.time())
    try:
        app.redis.hset(state,mapping={'status':'running','started_at':now,'error':''})
        await app.db.execute(rid,"SELECT MAX(id) FROM tracker_inbox WHERE provider='truckershub' AND processed_at IS NULL")
        receipt=(await app.db.fetchone(rid))[0]
        active=[]; exhausted=False
        for year,month in months_since():
            cachekey=f'truckershub-month:{year}-{month}'
            # Recent month on every tick. Old history is revisited daily for late uploads.
            current=(year,month)==(datetime.now(timezone.utc).year,datetime.now(timezone.utc).month)
            if not current and app.redis.get(cachekey) and not receipt:
                continue
            app.redis.hset(state,mapping={'month':f'{year}-{month:02d}'})
            async for summary in month_jobs(app,rid,key,year,month):
                if time.monotonic()-started>150 or counts['imported']+counts['failed']>=75:
                    exhausted=True;break
                if not lock.owned():
                    raise RuntimeError('Import lease expired')
                if not isinstance(summary,dict) or not str(summary.get('jobID','')).isdigit():
                    counts['failed']+=1;continue
                sid=int(summary['jobID']);counts['scanned']+=1
                await app.db.execute(rid,"SELECT logid,state,updated_at FROM delivery_source WHERE provider='truckershub' AND sourceid=%s",(sid,))
                existing=await app.db.fetchone(rid)
                if existing and existing[1]=='review':
                    counts['review']+=1;continue
                if existing and existing[0] is not None:
                    continue
                if existing and existing[1]=='failed' and now-existing[2]<300:
                    counts['failed']+=1;continue
                raw=summary
                try:
                    raw=await api_get(app,rid,key,f'jobs/{sid}')
                    if not isinstance(raw,dict) or str(raw.get('jobID'))!=str(sid):
                        raise ValueError('Job ID mismatch')
                    if job_status(raw)=='in_progress':
                        item=active_job(raw)
                        if item: active.append(item)
                        continue
                    result=await import_job(request,raw)
                    if len(result)==4: counts['imported']+=1
                    elif result[0]==409: counts['linked']+=1
                    elif result[0]==422: counts['review']+=1
                    else: raise ValueError('Job rejected')
                except Exception as exc:
                    await app.db.execute(rid,'ROLLBACK')
                    if isinstance(exc,RuntimeError) and str(exc).startswith('TruckersHub HTTP '):
                        raise
                    counts['failed']+=1
                    await save_source(app,rid,'truckershub',sid,None,'failed',compress(json.dumps(raw)))
                    await app.db.commit(rid)
                await asyncio.sleep(1.1)
            if exhausted: break
            if not current and not counts['failed']:
                app.redis.set(cachekey,'1',ex=86400)
        # Live endpoints can require a paid provider capability; history must still work.
        try:
            live=await api_get(app,rid,key,'live/delivery')
            if not isinstance(live,list): raise ValueError('Unexpected live response')
            for raw in live:
                item=active_job(raw)
                if item and item['trackerid']: active.append(item)
            app.redis.hset(state,mapping={'live_status':'available'})
            app.redis.set('truckershub-active',json.dumps({'updated_at':now,'list':active}),ex=86400)
        except Exception as exc:
            app.redis.hset(state,mapping={'live_status':'unavailable'})
            app.redis.set('truckershub-active',json.dumps({'updated_at':now,'list':active}),ex=86400)
        # Attach routes independently: route failures never roll back an imported job.
        await app.db.execute(rid,"SELECT s.sourceid,s.logid FROM delivery_source s JOIN dlog d ON d.logid=s.logid LEFT JOIN telemetry t ON t.logid=s.logid WHERE s.provider='truckershub' AND t.logid IS NULL AND s.route_retry_at<%s ORDER BY s.route_retry_at,s.updated_at LIMIT 20",(now,))
        for sid,lid in await app.db.fetchall(rid):
            if time.monotonic()-started>220 or not lock.owned(): break
            try:
                attached = await attach_route(app,rid,key,sid,lid)
                app.redis.set(f'truckershub-route:{sid}', 'available' if attached else 'missing', ex=3600)
            except Exception as exc:
                await app.db.execute(rid,'ROLLBACK')
                app.redis.set(f'truckershub-route:{sid}', 'restricted' if isinstance(exc,RuntimeError) and str(exc)=='TruckersHub HTTP 403' else 'unavailable', ex=3600)
            await app.db.execute(rid,"UPDATE delivery_source SET route_retry_at=%s WHERE provider='truckershub' AND sourceid=%s",(now+900,sid))
            await app.db.commit(rid)
            await asyncio.sleep(1.1)
        # Relink external records when the same Steam account later becomes a Hub driver.
        from functions.userinfo import checkPerm
        from functions.dataop import str2list
        await app.db.execute(rid,'SELECT steamid,userid,roles FROM user WHERE userid>=0 AND steamid IS NOT NULL')
        users={str(s):u for s,u,roles in await app.db.fetchall(rid) if checkPerm(app,str2list(roles),'driver')}
        await app.db.execute(rid,'SELECT logid,data FROM dlog WHERE tracker_type=6 AND userid=-1')
        for lid,payload in await app.db.fetchall(rid):
            steam=str(json.loads(decompress(payload))['data']['object']['driver']['steam_id'])
            if steam in users:
                await app.db.execute(rid,'UPDATE dlog SET userid=%s WHERE logid=%s AND userid=-1',(users[steam],lid))
                await app.db.execute(rid,'UPDATE telemetry SET userid=%s WHERE logid=%s',(users[steam],lid))
        if receipt and not exhausted and not counts['failed']:
            await app.db.execute(rid,"UPDATE tracker_inbox SET processed_at=%s WHERE provider='truckershub' AND id<=%s AND processed_at IS NULL",(now,receipt))
        await app.db.execute(rid,"DELETE FROM tracker_inbox WHERE provider='truckershub' AND processed_at IS NOT NULL AND processed_at<%s",(now-30*86400,))
        await app.db.commit(rid)
        app.redis.hset(state,mapping={**counts,'status':'partial' if exhausted or counts['failed'] or counts['review'] else 'complete','finished_at':int(time.time()),'error':''})
    except Exception as exc:
        await app.db.execute(rid,'ROLLBACK')
        error=str(exc) if isinstance(exc,RuntimeError) and str(exc).startswith('TruckersHub HTTP ') else type(exc).__name__
        app.redis.hset(state,mapping={**counts,'status':'failed','error':error,'finished_at':int(time.time())})
    finally:
        if lock.owned(): lock.release()


async def sync_loop(app):
    await asyncio.sleep(15)
    while True:
        rid=genrid();request=Request(scope={'type':'http','app':app,'headers':[],'mocked':True});request.state.dhrid=rid
        try:
            await app.db.new_conn(rid,db_name=app.config.db_name)
            await reconcile(request)
        except Exception as exc:
            logger.warning('TruckersHub reconciliation: %s',type(exc).__name__)
        finally:
            await app.db.close_conn(rid)
        await asyncio.sleep(60)
