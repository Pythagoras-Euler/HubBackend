"""Steam-verified TMP links, refreshed independently of delivery trackers."""
import asyncio
from functions.arequests import arequests
from functions.general import genrid
from logger import logger
from truckersmp_identity import verified_player


async def sync_user(app, rid, uid, steamid):
    steamid = str(steamid or '')
    if len(steamid) != 17 or not steamid.isascii() or not steamid.isdigit():
        return None
    key = f'tmp-identity:{uid}:{steamid}'
    cached = app.redis.get(key)
    if cached is not None:
        if not str(cached).isdigit():return None
        await app.db.execute(rid, 'UPDATE user SET truckersmpid=%s WHERE uid=%s AND steamid=%s', (int(cached),uid,steamid))
        await app.db.commit(rid)
        app.redis.delete(f'uinfo:{uid}')
        return int(cached)
    lock = app.redis.lock(f'tmp-identity-lock:{uid}', timeout=30, blocking=False)
    if not lock.acquire(blocking=False):
        return None
    try:
        response = await asyncio.wait_for(arequests.get(app, f'https://api.truckersmp.com/v2/player/{steamid}', dhrid=rid, timeout=10), timeout=12)
        if response.status_code != 200:
            app.redis.set(key, 'unavailable', ex=300)
            return None
        player = verified_player(response.json(), steamid)
        if player is None:
            app.redis.set(key, 'unavailable', ex=3600)
            return None
        tmpid = player['id']
        # The Steam account may have changed while the network request was pending.
        await app.db.execute(rid, 'UPDATE user SET truckersmpid=%s WHERE uid=%s AND steamid=%s', (tmpid, uid, steamid))
        await app.db.commit(rid)
        app.redis.delete(f'uinfo:{uid}')
        app.redis.set(key, str(tmpid), ex=86400)
        return tmpid
    except Exception as exc:
        await app.db.execute(rid, 'ROLLBACK')
        app.redis.set(key, 'unavailable', ex=300)
        logger.warning('TMP identity sync deferred: %s', type(exc).__name__)
        return None
    finally:
        if lock.owned():
            lock.release()


async def sync_loop(app):
    await asyncio.sleep(20)
    while True:
        rid = genrid()
        try:
            await app.db.new_conn(rid, db_name=app.config.db_name)
            await app.db.execute(rid, 'SELECT uid,steamid FROM user WHERE uid>=0 AND userid>=-1')
            users = await app.db.fetchall(rid)
            await app.db.commit(rid)
            for uid, steamid in users:
                await sync_user(app, rid, uid, steamid)
                from functions.avatars import refresh_avatar
                await refresh_avatar(app, rid, uid)
                await asyncio.sleep(1.1)
        except Exception as exc:
            logger.warning('TMP identity worker: %s', type(exc).__name__)
        finally:
            await app.db.close_conn(rid)
        await asyncio.sleep(300)
