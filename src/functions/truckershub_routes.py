"""Optional TruckersHub route enrichment, using only official read-only APIs."""
import asyncio
from datetime import datetime, timezone
import json
import time
from functions.arequests import arequests
from functions.dataop import compress, decompress
from truckershub_routes import same_delivery, route_points, encode_route, timestamp, api_payload


async def api_get(app, rid, key, path, include_links=False):
    response = await arequests.get(app, 'https://api.truckershub.in/v1/' + path,
                                  headers={'Authorization': key, 'Accept': 'application/json'}, dhrid=rid, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(f'TruckersHub HTTP {response.status_code}')
    raw = response.json()
    payload = api_payload(raw)
    return raw if include_links else payload


async def get_key(app, rid):
    await app.db.execute(rid, "SELECT sval FROM settings WHERE skey='truckershub/api_key'")
    row = await app.db.fetchone(rid)
    return row[0] if row else ''


async def sync_routes(request):
    app, rid = request.app, request.state.dhrid
    key = await get_key(app, rid)
    if not key or 'route' not in app.config.plugins:
        return
    lock = app.redis.lock('truckershub-route-lock', timeout=840, blocking=False)
    if not lock.acquire(blocking=False):
        return
    try:
        if app.redis.get('truckershub-route-cooldown'):
            return
        app.redis.set('truckershub-route-cooldown', '1', ex=900)
        app.redis.hset('truckershub-route-status', mapping={'status': 'running', 'started_at': int(time.time()), 'error': ''})
        await app.db.execute(rid, 'SELECT d.logid,d.userid,d.data FROM dlog d LEFT JOIN telemetry t ON t.logid=d.logid WHERE d.logid>0 AND t.logid IS NULL')
        rows = await app.db.fetchall(rid)
        candidates, months = [], set()
        for logid, userid, raw in rows:
            obj = json.loads(decompress(raw))['data']['object']
            start, end = timestamp(obj.get('start_time')), timestamp(obj.get('stop_time'))
            if start is None or end is None:
                continue
            candidates.append((logid, userid, obj))
            for stamp in (start, end):
                dt = datetime.fromtimestamp(stamp, timezone.utc)
                months.add((dt.year, dt.month))
        jobs = {}
        for year, month in sorted(months, reverse=True):
            if not lock.owned():
                raise RuntimeError('Route sync lease expired')
            data = await api_get(app, rid, key, f'jobs?month={month}&year={year}')
            if not isinstance(data, list):
                raise ValueError('Unexpected TruckersHub jobs response')
            for job in data:
                if isinstance(job, dict) and job.get('jobID') is not None:
                    jobs[str(job['jobID'])] = job
            await asyncio.sleep(1.1)
        linked = 0
        for logid, userid, obj in candidates:
            matches = [job for job in jobs.values() if same_delivery(obj, job)]
            if len(matches) != 1:
                continue
            if sum(same_delivery(other, matches[0]) for _, _, other in candidates) != 1:
                continue
            if not lock.owned():
                raise RuntimeError('Route sync lease expired')
            job_id = int(matches[0]['jobID'])
            points = route_points(await api_get(app, rid, key, f'routes/{job_id}'))
            if len(points) < 2:
                continue
            data = encode_route(obj['game']['short_name'], points)
            # Lock the delivery row; never replace an existing route or reuse one for another job.
            await app.db.execute(rid, 'SELECT logid FROM dlog WHERE logid=%s FOR UPDATE', (logid,))
            await app.db.fetchone(rid)
            await app.db.execute(rid, 'SELECT logid FROM telemetry WHERE logid=%s OR uuid=%s', (logid, f'truckershub:{job_id}'))
            if not await app.db.fetchone(rid):
                await app.db.execute(rid, 'INSERT INTO telemetry(logid,uuid,userid,data) VALUES (%s,%s,%s,%s)', (logid, f'truckershub:{job_id}', userid, compress(data)))
                linked += 1
            await app.db.commit(rid)
            await asyncio.sleep(1.1)
        app.redis.hset('truckershub-route-status', mapping={'status': 'complete', 'finished_at': int(time.time()), 'linked': linked, 'error': ''})
    except Exception as exc:
        await app.db.execute(rid, 'ROLLBACK')
        error = str(exc) if isinstance(exc, RuntimeError) and str(exc).startswith('TruckersHub HTTP ') else type(exc).__name__
        app.redis.hset('truckershub-route-status', mapping={'status': 'failed', 'finished_at': int(time.time()), 'error': error})
    finally:
        if lock.owned():
            lock.release()
