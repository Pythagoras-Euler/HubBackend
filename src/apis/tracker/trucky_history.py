import json
import time
from typing import Optional
from fastapi import Header, Request, Response

from functions import auth, ratelimit
from functions.trucky_sync import driver_totals
from truckershub_import import merge_active


async def get_drivers(request: Request, response: Response, authorization: str = Header(None),
                      after: Optional[int] = None, before: Optional[int] = None):
    app, rid = request.app, request.state.dhrid
    limited, result = await ratelimit(request, 'GET /trucky/drivers', 60, 30)
    if limited:
        return result
    await app.db.new_conn(rid, db_name=app.config.db_name)
    if app.config.privacy:
        au = await auth(authorization, request, allow_application_token=True)
        if au['error']:
            response.status_code = au.pop('code')
            return au
    conditions = ['tracker_type IN (3,6)', 'logid>=0']
    if after is not None:
        conditions.append(f'timestamp>={after}')
    if before is not None:
        conditions.append(f'timestamp<={before}')
    await app.db.execute(rid, 'SELECT logid,userid,data,unit,distance FROM dlog WHERE ' + ' AND '.join(conditions) + ' ORDER BY timestamp')
    drivers = driver_totals(await app.db.fetchall(rid))
    return {'list': drivers, 'total_items': len(drivers)}


async def get_sync_status(request: Request, response: Response, authorization: str = Header(None)):
    app, rid = request.app, request.state.dhrid
    await app.db.new_conn(rid, db_name=app.config.db_name)
    au = await auth(authorization, request, allow_application_token=True,
                    required_permission=['administrator', 'import_dlogs'])
    if au['error']:
        response.status_code = au.pop('code')
        return au
    return {'interval_seconds': 90, 'companies': [
        {'company_id': t['company_id'], **app.redis.hgetall(f"trucky-sync:{int(t['company_id'])}")}
        for t in app.config.trackers if t['type'] == 'trucky' and t.get('company_id')]}


async def get_active_jobs(request: Request, response: Response, authorization: str = Header(None)):
    app, rid = request.app, request.state.dhrid
    limited, result = await ratelimit(request, 'GET /trucky/active', 60, 60)
    if limited:
        return result
    if app.config.privacy:
        await app.db.new_conn(rid, db_name=app.config.db_name)
        au = await auth(authorization, request, allow_application_token=True)
        if au['error']:
            response.status_code = au.pop('code')
            return au
    jobs = {}
    for tracker in app.config.trackers:
        if tracker['type'] != 'trucky' or not tracker.get('company_id'):
            continue
        raw = app.redis.get(f"trucky-active:{int(tracker['company_id'])}")
        if not raw:
            continue
        snapshot = json.loads(raw)
        for item in snapshot['list']:
            jobs['trucky:'+str(item['trackerid'])] = {**item, 'tracker':'trucky', 'stale': time.time()-snapshot['updated_at'] > 300}
    raw=app.redis.get('truckershub-active')
    if raw:
        snapshot=json.loads(raw)
        for item in snapshot.get('list',[]):
            jobs['truckershub:'+str(item['trackerid'])]={**item,'tracker':'truckershub','stale':time.time()-snapshot['updated_at']>300}
    return {'list': sorted(merge_active(jobs.values()), key=lambda j: j.get('start_time') or '', reverse=True)}
