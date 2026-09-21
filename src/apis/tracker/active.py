import json,time
from fastapi import Header,Request,Response
from functions import auth,checkPerm
from active_deliveries import can_abandon


async def identity(request,response,authorization,required=False):
    if not authorization and not required and not request.app.config.privacy:return None
    user=await auth(authorization,request,allow_application_token=True)
    if user['error']:
        response.status_code=user.pop('code');return user
    return user


async def get_jobs(request:Request,response:Response,include_closed:bool=False,authorization:str=Header(None)):
    app,rid=request.app,request.state.dhrid
    await app.db.new_conn(rid,db_name=app.config.db_name)
    user=await identity(request,response,authorization)
    if user and user.get('error'):return user
    staff=bool(user and checkPerm(app,user['roles'],['administrator','delete_dlogs']))
    clause="status IN ('abandoned','aborted','duplicate')" if include_closed else "status='in_progress'"
    await app.db.execute(rid,'SELECT provider,sourceid,status,payload,closed_at FROM active_delivery WHERE '+clause+' ORDER BY first_seen DESC LIMIT 500')
    items=[]
    for provider,sid,status,payload,closed in await app.db.fetchall(rid):
        job=json.loads(payload)
        items.append({**job,'tracker':provider,'trackerid':sid,'status':status,'closed_at':closed,
                      'can_abandon':status=='in_progress' and can_abandon(user,job,staff)})
    return {'list':items,'timeout_days':getattr(app.config,'active_delivery_timeout_days',7)}


async def abandon(request:Request,response:Response,provider:str,sourceid:int,authorization:str=Header(None)):
    app,rid=request.app,request.state.dhrid
    await app.db.new_conn(rid,db_name=app.config.db_name)
    user=await identity(request,response,authorization,True)
    if user.get('error'):return user
    try:data=await request.json()
    except ValueError:data={}
    if not isinstance(data,dict) or data.get('confirmed') is not True:
        response.status_code=422;return {'error':'Confirmation required'}
    if provider not in ('trucky','truckershub') or not 0<sourceid<2**63:
        response.status_code=422;return {'error':'Invalid source'}
    lock=app.redis.lock('active-delivery-lifecycle',timeout=60,blocking=False)
    if not lock.acquire(blocking=False):
        response.status_code=409;return {'error':'Sync in progress; try again'}
    try:
        await app.db.execute(rid,'SELECT status,payload FROM active_delivery WHERE provider=%s AND sourceid=%s FOR UPDATE',(provider,sourceid))
        row=await app.db.fetchone(rid)
        if not row:
            response.status_code=404;return {'error':'Transport not found'}
        job=json.loads(row[1])
        if not can_abandon(user,job,checkPerm(app,user['roles'],['administrator','delete_dlogs'])):
            response.status_code=403;return {'error':'Permission denied'}
        if row[0]!='in_progress':
            response.status_code=409;return {'error':'Transport is no longer active'}
        await app.db.execute(rid,"UPDATE active_delivery SET status='abandoned',closed_at=%s,closed_by=%s WHERE provider=%s AND sourceid=%s AND status='in_progress'",(int(time.time()),user['uid'],provider,sourceid))
        await app.db.commit(rid)
        return {'status':'abandoned'}
    finally:
        # End read-only transactions on denied/stale requests as well.
        try:await app.db.execute(rid,'ROLLBACK')
        finally:
            if lock.owned():lock.release()
