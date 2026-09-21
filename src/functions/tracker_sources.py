"""Shared source idempotency and cross-provider delivery reconciliation."""
import hashlib
import json
import time
from functions.dataop import compress, decompress
from truckershub_routes import timestamp


def same_trip(a, b):
    if a.get('driver',{}).get('steam_id') is None:
        return False
    if str(a['driver']['steam_id']) != str(b.get('driver',{}).get('steam_id')):
        return False
    if a.get('game',{}).get('short_name') != b.get('game',{}).get('short_name'):
        return False
    for key in ('source_city','source_company','destination_city','destination_company','cargo'):
        left, right = (a.get(key) or {}).get('unique_id'), (b.get(key) or {}).get('unique_id')
        if not left or not right or str(left).casefold() != str(right).casefold():
            return False
    for key in ('start_time','stop_time'):
        x,y=timestamp(a.get(key)),timestamp(b.get(key))
        if x is None or y is None or abs(x-y)>120:
            return False
    return True


async def save_source(app, rid, provider, sourceid, logid, state='linked', payload=None):
    await app.db.execute(rid, """INSERT INTO delivery_source(provider,sourceid,logid,state,payload,updated_at)
        VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
        logid=VALUES(logid),state=VALUES(state),payload=COALESCE(VALUES(payload),payload),updated_at=VALUES(updated_at)""",
        (provider,sourceid,logid,state,payload,int(time.time())))


async def ingest(request, original, converted, provider, core, **options):
    app,rid=request.app,request.state.dhrid
    data=converted['data']['object']; sid=int(data['id']); steam=str(data['driver']['steam_id'])
    # Finish the caller's read snapshot before waiting; named locks survive commits.
    await app.db.commit(rid)
    await app.db.extend_conn(rid,15)
    names=['hub-source:'+hashlib.sha256((app.config.db_name+':'+provider+':'+str(sid)).encode()).hexdigest()[:45],
           'hub-driver:'+hashlib.sha256((app.config.db_name+':'+steam).encode()).hexdigest()[:45]]
    acquired=[]
    try:
        for name in names:
            await app.db.execute(rid,'SELECT GET_LOCK(%s,5)',(name,))
            locked=await app.db.fetchone(rid)
            if not locked or locked[0] != 1:
                return (503,'Delivery import busy; retry')
            acquired.append(name)
        await app.db.execute(rid,'SELECT logid,state FROM delivery_source WHERE provider=%s AND sourceid=%s',(provider,sid))
        link=await app.db.fetchone(rid)
        if link and link[0] is not None:
            return (409,'Already logged or deliberately deleted')
        if link and link[1] == 'review':
            return (422,'Possible duplicate requires administrator review')
        tracker_type=3 if provider=='trucky' else 6
        await app.db.execute(rid,'SELECT logid FROM dlog_deleted WHERE tracker_type=%s AND trackerid=%s',(tracker_type,sid))
        if await app.db.fetchone(rid):
            return (409,'Delivery was deliberately deleted')
        await app.db.execute(rid,'SELECT logid FROM dlog WHERE tracker_type=%s AND trackerid=%s',(tracker_type,sid))
        existing=await app.db.fetchone(rid)
        if existing:
            await save_source(app,rid,provider,sid,existing[0])
            await app.db.commit(rid)
            return (409,'Already logged')
        if not link or link[1] != 'separate':
            stop=int(timestamp(data['stop_time']))
            await app.db.execute(rid,'SELECT logid,data,isdelivered FROM dlog WHERE tracker_type=%s AND timestamp BETWEEN %s AND %s UNION ALL SELECT logid,data,isdelivered FROM dlog_deleted WHERE tracker_type=%s AND timestamp BETWEEN %s AND %s', (6 if provider=='trucky' else 3,stop-120,stop+120)*2)
            matches=[]; possible=False
            for lid,raw,delivered in await app.db.fetchall(rid):
                other=json.loads(decompress(raw))['data']['object']
                if str(other.get('driver',{}).get('steam_id')) != steam:
                    continue
                if other.get('game',{}).get('short_name') != data.get('game',{}).get('short_name'):
                    continue
                if timestamp(other.get('start_time')) is None or timestamp(data.get('start_time')) is None:
                    possible=True
                    continue
                if abs(timestamp(other['start_time'])-timestamp(data['start_time']))>120:
                    continue
                possible=True
                if same_trip(data,other) and bool(delivered)==(converted['type']=='job.delivered'):
                    matches.append(lid)
            if len(matches)==1:
                await save_source(app,rid,provider,sid,matches[0],payload=compress(json.dumps(original)))
                await app.db.commit(rid)
                return (409,'Linked to existing delivery')
            if possible:
                await save_source(app,rid,provider,sid,None,'review',compress(json.dumps(original)))
                await app.db.commit(rid)
                return (422,'Possible duplicate requires administrator review')
        return await core(request,original,converted,provider,**options)
    except Exception:
        await app.db.execute(rid,'ROLLBACK')
        raise
    finally:
        for name in reversed(acquired):
            await app.db.execute(rid,'SELECT RELEASE_LOCK(%s)',(name,))
            await app.db.fetchone(rid)
