import hashlib
import json
from functions.dataop import compress, decompress
import hmac
import time
from truckershub_routes import webhook_token
from fastapi import Header, Request, Response
from apis.tracker.trucky_roles import authorize
from functions.truckershub_routes import api_get, get_key


async def get_settings(request: Request, response: Response, authorization: str = Header(None)):
    denied = await authorize(request, response, authorization)
    if denied:
        return denied
    app, rid = request.app, request.state.dhrid
    key = await get_key(app, rid)
    await app.db.execute(rid,"SELECT provider,sourceid,state,updated_at,payload FROM delivery_source WHERE provider IN ('truckershub','trucky') AND state IN ('failed','review') ORDER BY updated_at DESC LIMIT 50")
    issues=[]
    for provider,sid,state,updated,payload in await app.db.fetchall(rid):
        raw=json.loads(decompress(payload)) if payload else {}
        if not isinstance(raw,dict): raw={}
        if provider=='trucky':
            d=raw.get('data') or {}
            raw={'driver':{'username':(d.get('driver') or {}).get('name')},'source':{'city':{'name':d.get('source_city_name')}},'destination':{'city':{'name':d.get('destination_city_name')}},'cargo':{'name':d.get('cargo_name')},'realtime':{'start':d.get('started_at')}}
        issues.append({'provider':provider,'sourceid':sid,'state':state,'updated_at':updated,
                       'driver':(raw.get('driver') or {}).get('username'),
                       'source':((raw.get('source') or {}).get('city') or {}).get('name'),
                       'destination':((raw.get('destination') or {}).get('city') or {}).get('name'),
                       'cargo':(raw.get('cargo') or {}).get('name'),
                       'start_time':(raw.get('realtime') or {}).get('start')})
    return {'configured': bool(key), 'sync': app.redis.hgetall('truckershub-import-status'), 'issues':issues,
            'webhook_path': '/truckershub/update?token=' + webhook_token(key) if key else None}


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
        app.redis.delete('truckershub-active')
        app.redis.delete('truckershub-route-cooldown')
        app.redis.hset('truckershub-import-status', mapping={'status': 'pending' if key else 'disabled', 'error': ''})
    finally:
        if lock.owned():
            lock.release()
    return {'configured': bool(key), 'webhook_path': '/truckershub/update?token=' + webhook_token(key) if key else None}


async def post_update(request: Request, response: Response, token: str = ''):
    """Persist a notification before scheduling authenticated job reconciliation."""
    app, rid = request.app, request.state.dhrid
    await app.db.new_conn(rid, db_name=app.config.db_name)
    key = await get_key(app, rid)
    if not key or not token.isascii() or not hmac.compare_digest(token, webhook_token(key)):
        response.status_code = 403
        return {'error': 'Invalid webhook token'}
    raw=b''
    async for chunk in request.stream():
        raw+=chunk
        if len(raw)>1048576:
            response.status_code=413
            return {'error':'Webhook payload too large'}
    try:
        payload=json.loads(raw or b'{}')
        if not isinstance(payload,dict): raise ValueError()
    except (ValueError,UnicodeDecodeError):
        response.status_code=400
        return {'error':'Invalid webhook JSON'}
    # Commit a durable receipt before acknowledging. Only authenticated API data
    # is imported; notifications cannot forge a delivery or assign another driver.
    digest=hashlib.sha256(raw).hexdigest()
    await app.db.execute(rid,"INSERT IGNORE INTO tracker_inbox(provider,digest,payload,received_at) VALUES ('truckershub',%s,%s,%s)",
                         (digest,compress(json.dumps(payload)),int(time.time())))
    await app.db.commit(rid)
    app.redis.hset('truckershub-import-status', mapping={'webhook_received_at': int(time.time())})
    response.status_code = 200
    return {'received': True}


async def post_retry(request: Request, response: Response, authorization: str = Header(None)):
    denied=await authorize(request,response,authorization)
    if denied: return denied
    app,rid=request.app,request.state.dhrid
    data=await request.json()
    sid=data.get('sourceid') if isinstance(data,dict) else None
    provider=data.get('provider','truckershub') if isinstance(data,dict) else None
    if provider not in ('trucky','truckershub') or isinstance(sid,bool) or not str(sid).isascii() or not str(sid).isdigit() or not 0<int(sid)<2**63:
        response.status_code=422;return {'error':'Invalid source ID'}
    sid=int(sid)
    await app.db.execute(rid,"SELECT state,payload FROM delivery_source WHERE provider=%s AND sourceid=%s",(provider,sid))
    row=await app.db.fetchone(rid)
    if not row or row[0] not in ('failed','review'):
        response.status_code=409;return {'error':'No pending issue'}
    if row[0]=='review' and data.get('decision') == 'link':
        from public_id_store import resolve_public_id
        try: lid=await resolve_public_id(app,rid,str(data.get('public_id','')).strip())
        except ValueError: lid=None
        if lid is None:
            response.status_code=422;return {'error':'Invalid Public ID'}
        await app.db.execute(rid,"SELECT data FROM dlog WHERE logid=%s",(lid,))
        target=await app.db.fetchone(rid)
        if not target:
            response.status_code=409;return {'error':'Delivery no longer exists'}
        local=json.loads(decompress(target[0]))['data']['object']
        remote=json.loads(decompress(row[1]))
        if provider=='trucky':
            remote=remote.get('data') or {}
            steam=((remote.get('driver') or {}).get('steam_profile') or {}).get('steam_id')
        else: steam=(remote.get('driver') or {}).get('steamID')
        if str(local['driver']['steam_id']) != str(steam):
            response.status_code=422;return {'error':'Driver identity mismatch'}
        await app.db.execute(rid,"UPDATE delivery_source SET logid=%s,state='linked',updated_at=%s WHERE provider=%s AND sourceid=%s AND state='review'",(lid,int(time.time()),provider,sid))
        await app.db.commit(rid)
        return {'queued':True}
    if row[0]=='review' and data.get('decision') != 'separate':
        response.status_code=422;return {'error':'Confirm separate delivery before importing'}
    state='separate' if row[0]=='review' else 'pending'
    await app.db.execute(rid,"UPDATE delivery_source SET state=%s,updated_at=0 WHERE provider=%s AND sourceid=%s AND state=%s",(state,provider,sid,row[0]))
    await app.db.commit(rid)
    for key in app.redis.scan_iter(match='truckershub-month:*'):
        app.redis.delete(key)
    return {'queued':True}
