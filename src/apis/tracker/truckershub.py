from fastapi import Header, Request, Response
from apis.tracker.trucky_roles import authorize
from functions.truckershub_routes import api_get, get_key


async def get_settings(request: Request, response: Response, authorization: str = Header(None)):
    denied = await authorize(request, response, authorization)
    if denied:
        return denied
    app, rid = request.app, request.state.dhrid
    return {'configured': bool(await get_key(app, rid)), 'sync': app.redis.hgetall('truckershub-route-status')}


async def put_settings(request: Request, response: Response, authorization: str = Header(None)):
    denied = await authorize(request, response, authorization)
    if denied:
        return denied
    app, rid = request.app, request.state.dhrid
    try:
        data = await request.json()
        if not isinstance(data, dict) or not isinstance(data.get('api_key'), str):
            raise ValueError('API Token is required')
        key = data['api_key'].strip()
        if len(key) > 2048 or '\r' in key or '\n' in key:
            raise ValueError('Invalid API Token')
        if key:
            company = await api_get(app, rid, key, 'me')
            if not isinstance(company, dict) or not company.get('id'):
                raise ValueError('Unexpected TruckersHub company response')
    except ValueError as exc:
        response.status_code = 422
        return {'error': str(exc)}
    except Exception:
        response.status_code = 502
        return {'error': 'TruckersHub authentication failed'}
    lock = app.redis.lock('truckershub-settings-lock', timeout=10, blocking=False)
    if not lock.acquire(blocking=False):
        response.status_code = 409
        return {'error': 'Configuration is busy'}
    try:
        await app.db.execute(rid, "DELETE FROM settings WHERE skey='truckershub/api_key'")
        if key:
            await app.db.execute(rid, "INSERT INTO settings(uid,skey,sval) VALUES (0,'truckershub/api_key',%s)", (key,))
        await app.db.commit(rid)
        app.redis.delete('truckershub-route-cooldown')
        app.redis.hset('truckershub-route-status', mapping={'status': 'pending' if key else 'disabled', 'error': ''})
    finally:
        if lock.owned():
            lock.release()
    return {'configured': bool(key)}
