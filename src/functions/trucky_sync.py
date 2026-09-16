"""Company history reconciliation and Steam-based delivery identities."""
import asyncio
import json
import time

from fastapi import Request
from functions.arequests import arequests
from functions.dataop import decompress
from functions.general import genrid
from logger import logger


def driver_totals(rows):
    """Keep external drivers separate; never group all userid=-1 jobs together."""
    drivers = {}
    for logid, userid, raw, unit, distance in rows:
        obj = json.loads(decompress(raw))["data"]["object"]
        driver = obj["driver"]
        steamid = str(driver["steam_id"])
        item = drivers.setdefault(steamid, {
            "steamid": steamid, "name": driver["username"],
            "avatar": driver.get("profile_photo_url"), "userid": None,
            "jobs": 0, "ets2_jobs": 0, "ats_jobs": 0, "distance": 0,
        })
        if userid >= 0:
            item["userid"] = userid
        item["jobs"] += 1
        item["ets2_jobs" if unit == 1 else "ats_jobs"] += 1
        item["distance"] += distance or 0
    return sorted(drivers.values(), key=lambda d: (-d["distance"], d["steamid"]))


async def source_json(app, request_id, path, token):
    response = await arequests.get(app, 'https://e.truckyapp.com/api/v1/' + path,
                                   headers={"X-ACCESS-TOKEN": token, "Accept": "application/json"},
                                   dhrid=request_id)
    if response.status_code != 200:
        # Do not persist response bodies, which may contain private details.
        raise RuntimeError(f"Trucky HTTP {response.status_code}")
    return response.json()


async def reconcile(app, tracker, lock):
    from apis.tracker.trucky import convert_format
    from functions.tracker import handle_new_job
    rid = genrid()
    request = Request(scope={"type": "http", "app": app, "headers": [], "mocked": True})
    request.state.dhrid = rid
    company = int(tracker['company_id'])
    state_key = f'trucky-sync:{company}'
    counts = {"scanned": 0, "imported": 0, "existing": 0, "failed": 0}
    app.redis.hset(state_key, mapping={"status": "running", "started_at": int(time.time())})
    try:
        await app.db.new_conn(rid, db_name=app.config.db_name)
        # Respect explicitly deleted deliveries during automatic reconciliation.
        await app.db.execute(rid, 'SELECT trackerid FROM dlog WHERE tracker_type=3 UNION SELECT trackerid FROM dlog_deleted WHERE tracker_type=3')
        known = {int(row[0]) for row in await app.db.fetchall(rid)}
        seen = set()
        page = 1
        while True:
            if not lock.owned():
                raise RuntimeError('Reconciliation lease expired; retry next run')
            listing = await source_json(app, rid, f'company/{company}/jobs?page={page}', tracker['api_token'])
            jobs = listing.get('data')
            if not isinstance(jobs, list) or 'total' not in listing or not listing.get('per_page'):
                raise ValueError('Unexpected Trucky pagination response')
            for job in jobs:
                jid = int(job['id'])
                if jid in seen:
                    continue
                seen.add(jid)
                counts['scanned'] += 1
                if jid in known:
                    counts['existing'] += 1
                    continue
                if not lock.owned():
                    raise RuntimeError('Reconciliation lease expired; retry next run')
                try:
                    data = await source_json(app, rid, f'job/{jid}', tracker['api_token'])
                    if data['status'] not in ('completed', 'canceled'):
                        continue
                    data['events'] = await source_json(app, rid, f'job/{jid}/events', tracker['api_token'])
                    original = {"event": 'job_' + data['status'], "data": data}
                    result = await handle_new_job(request, original, convert_format(original), 'trucky',
                                                bypass_tracker_check=True, allow_external_driver=True,
                                                historical=True)
                    if len(result) != 2:
                        known.add(jid)
                        counts['imported'] += 1
                    elif result[0] == 409:
                        counts['existing'] += 1
                    else:
                        raise RuntimeError(f'Import returned {result[0]}')
                except Exception as exc:
                    counts['failed'] += 1
                    app.redis.hset(state_key, mapping={"last_failed_job": jid, "last_error": str(exc)[:300]})
                    # Upstream blocks/rate limits should not trigger a request storm.
                    if isinstance(exc, RuntimeError) and str(exc).startswith('Trucky HTTP'):
                        raise
                await asyncio.sleep(1.1)
            if page * int(listing['per_page']) >= int(listing['total']):
                break
            if not jobs:
                raise ValueError('Trucky returned an empty page before end of history')
            page += 1
        app.redis.hset(state_key, mapping={**counts, "status": "partial" if counts['failed'] else "complete",
                                          "finished_at": int(time.time())})
        if not counts['failed']:
            app.redis.hset(state_key, mapping={"last_success_at": int(time.time()), "last_error": ""})
    except Exception as exc:
        app.redis.hset(state_key, mapping={**counts, "status": "failed", "last_error": str(exc)[:300],
                                          "finished_at": int(time.time())})
        logger.warning('Trucky company %s reconciliation failed: %s', company, exc)
    finally:
        await app.db.close_conn(rid)


async def sync_loop(app):
    await asyncio.sleep(15)
    while True:
        for tracker in app.config.trackers:
            if tracker['type'] != 'trucky' or not tracker.get('company_id') or not tracker.get('api_token'):
                continue
            lock = app.redis.lock(f"trucky-sync-lock:{int(tracker['company_id'])}", timeout=840, blocking=False)
            try:
                if lock.acquire(blocking=False):
                    try:
                        await reconcile(app, tracker, lock)
                    finally:
                        if lock.owned():
                            lock.release()
            except Exception as exc:
                logger.warning('Trucky reconciliation worker: %s', exc)
        await asyncio.sleep(900)
