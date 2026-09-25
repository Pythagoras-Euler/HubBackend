import json
import re
from fastapi import Header, Request, Response
from avatar_images import MAX_BYTES
from functions import auth, ratelimit
from functions.avatars import save_avatar


async def get_avatar_settings(request: Request, response: Response, authorization: str = Header(None)):
    app, rid = request.app, request.state.dhrid
    await app.db.new_conn(rid, db_name=app.config.db_name)
    user = await auth(authorization, request, check_member=False)
    if user['error']:
        response.status_code=user.pop('code');return user
    await app.db.execute(rid, 'SELECT source,source_url FROM avatar_profile WHERE uid=%s', (user['uid'],))
    row = await app.db.fetchone(rid)
    return {'source':row[0] if row else 'existing', 'url':row[1] if row else '', 'max_bytes':MAX_BYTES}


async def put_avatar(request: Request, response: Response, source: str, authorization: str = Header(None)):
    app, rid = request.app, request.state.dhrid
    await app.db.new_conn(rid, db_name=app.config.db_name)
    user = await auth(authorization, request, check_member=False)
    if user['error']:
        response.status_code=user.pop('code');return user
    limited = await ratelimit(request, 'PUT /user/avatar', 60, 5)
    if limited[0]:return limited[1]
    if source not in ('truckersmp','steam','discord','external','upload'):
        response.status_code=422;return {'error':'Invalid avatar source'}
    limit = MAX_BYTES if source == 'upload' else 4096
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>limit:
            response.status_code=413;return {'error':'Image exceeds size limit'}
    try:
        url = json.loads(data or b'{}').get('url') if source == 'external' else None
        avatar = await save_avatar(app, rid, user['uid'], source, url=url, content=bytes(data) if source=='upload' else None)
        return {'avatar':avatar,'source':source}
    except (ValueError, AttributeError) as exc:
        response.status_code=422;return {'error':str(exc)}
    except Exception:
        response.status_code=503;return {'error':'Avatar source unavailable; current avatar preserved'}


async def get_image(request: Request, uid: int, digest: str):
    if uid<0 or not re.fullmatch(r'[a-f0-9]{64}',digest):return Response(status_code=404)
    app, rid=request.app,request.state.dhrid
    await app.db.new_conn(rid, db_name=app.config.db_name)
    await app.db.execute(rid, 'SELECT a.image FROM avatar_profile a JOIN user u ON u.uid=a.uid WHERE a.uid=%s AND a.digest=%s', (uid,digest))
    row=await app.db.fetchone(rid)
    if not row:return Response(status_code=404)
    return Response(content=bytes(row[0]),media_type='image/png',headers={'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox",'Cache-Control':'public, max-age=86400','Content-Disposition':'inline; filename="avatar.png"'})
