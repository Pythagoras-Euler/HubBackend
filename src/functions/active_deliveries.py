"""Persist local lifecycle independently of provider snapshots and completed-job totals."""
import asyncio,json,time
from truckershub_routes import timestamp
from active_deliveries import same_trip,trip_from_delivery,expired
from functions.dataop import decompress
from functions.general import genrid
from logger import logger


def snapshots(app):
    result={}
    keys=[f"trucky-active:{int(t['company_id'])}" for t in app.config.trackers if t['type']=='trucky' and t.get('company_id')]
    keys.append('truckershub-active')
    for key in keys:
        raw=app.redis.get(key)
        if not raw:continue
        snapshot=json.loads(raw)
        provider='trucky' if key.startswith('trucky') else 'truckershub'
        for job in snapshot.get('list',[]):
            # Wait for the upgraded feed instead of closing legacy cached jobs before identity matching.
            if 'game' not in job:continue
            sid=str(job.get('trackerid',''))
            if not sid.isascii() or not sid.isdigit() or not 0<int(sid)<2**63:continue
            result[(provider,int(sid))]={**job,'tracker':provider,'trackerid':int(sid),'stale':time.time()-snapshot.get('updated_at',0)>300}
    return result


async def reconcile(app,rid):
    lock=app.redis.lock('active-delivery-lifecycle',timeout=60,blocking=False)
    if not lock.acquire(blocking=False):return
    try:
        now=int(time.time());days=getattr(app.config,'active_delivery_timeout_days',7)
        for (provider,sid),job in snapshots(app).items():
            await app.db.execute(rid,"""INSERT INTO active_delivery(provider,sourceid,steamid,status,payload,first_seen,updated_at)
                VALUES (%s,%s,%s,'in_progress',%s,%s,%s) ON DUPLICATE KEY UPDATE payload=VALUES(payload),steamid=VALUES(steamid),updated_at=VALUES(updated_at)""",
                (provider,sid,job.get('steamid') or '',json.dumps(job),now,now))
        await app.db.execute(rid,'SELECT provider,sourceid,status,payload,first_seen FROM active_delivery ORDER BY first_seen,provider,sourceid')
        rows=await app.db.fetchall(rid)
        active=[(p,s,json.loads(raw),first) for p,s,status,raw,first in rows if status=='in_progress']
        closed=[(json.loads(raw),status) for p,s,status,raw,first in rows if status in ('abandoned','aborted')]
        completed=[]
        if active:
            oldest=min((timestamp(job.get('start_time')) or first) for p,s,job,first in active)
            await app.db.execute(rid,'SELECT logid,data,tracker_type,trackerid FROM dlog WHERE timestamp>=%s UNION ALL SELECT logid,data,tracker_type,trackerid FROM dlog_deleted WHERE timestamp>=%s',(int(oldest)-120,int(oldest)-120))
            completed=[(lid,trip_from_delivery(json.loads(decompress(raw))['data']['object']),tt,sid) for lid,raw,tt,sid in await app.db.fetchall(rid)]
        keep=[]
        for provider,sid,job,first in active:
            exact=[lid for lid,other,tt,other_sid in completed if tt=={'trucky':3,'truckershub':6}[provider] and other_sid is not None and int(other_sid)==sid]
            matches=[lid for lid,other,tt,other_sid in completed if same_trip(job,other)]
            terminal=(exact[0],'completed') if len(exact)==1 else ((matches[0],'duplicate') if len(matches)==1 else None)
            status=None;matched=None
            if terminal:matched,status=terminal
            elif any(same_trip(job,other) for other,state in closed):status='aborted'
            elif expired(job,first,now,days):status='aborted'
            elif any(same_trip(job,other) for other in keep):status='duplicate'
            else:keep.append(job)
            if status:
                await app.db.execute(rid,"UPDATE active_delivery SET status=%s,closed_at=%s,matched_logid=%s WHERE provider=%s AND sourceid=%s AND status='in_progress'",(status,now,matched,provider,sid))
        await app.db.commit(rid)
    except Exception:
        await app.db.execute(rid,'ROLLBACK')
        raise
    finally:
        if lock.owned():lock.release()


async def sync_loop(app):
    await asyncio.sleep(20)
    while True:
        rid=genrid()
        try:
            await app.db.new_conn(rid,db_name=app.config.db_name)
            await reconcile(app,rid)
        except Exception as exc:logger.warning('Active delivery lifecycle: %s',type(exc).__name__)
        finally:await app.db.close_conn(rid)
        await asyncio.sleep(60)
