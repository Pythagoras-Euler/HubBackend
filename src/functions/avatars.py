import asyncio
import hashlib
import os
import time
from avatar_images import fetch_image, normalize_image
from functions.arequests import arequests
from truckersmp_identity import verified_player

_decode_slots = asyncio.Semaphore(2)


async def provider_url(app, rid, source, steamid, discordid):
    if source == 'truckersmp':
        if not steamid:raise ValueError('Connect Steam first')
        r = await arequests.get(app, f'https://api.truckersmp.com/v2/player/{steamid}', dhrid=rid, timeout=10)
        player = verified_player(r.json(), steamid) if r.status_code == 200 else None
        if not player:raise ValueError('TMP account unavailable for this Steam ID')
        return player.get('avatar')
    if source == 'steam':
        if not steamid or not app.config.steam_api_key:raise ValueError('Steam account or Steam API configuration unavailable')
        r = await arequests.get(app, f'https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={app.config.steam_api_key}&steamids={steamid}', dhrid=rid, timeout=10)
        players = r.json().get('response', {}).get('players', []) if r.status_code == 200 else []
        player = next((p for p in players if str(p.get('steamid')) == str(steamid)), None)
        if not player:raise ValueError('Steam profile unavailable')
        return player.get('avatarfull')
    if source == 'discord':
        if not discordid or not app.config.discord_bot_token:raise ValueError('Discord account or integration unavailable')
        r = await arequests.get(app, f'https://discord.com/api/v10/users/{discordid}', headers={'Authorization':f'Bot {app.config.discord_bot_token}'}, dhrid=rid, timeout=10)
        player = r.json() if r.status_code == 200 else {}
        if str(player.get('id')) != str(discordid):raise ValueError('Discord profile unavailable')
        avatar = player.get('avatar')
        return f'https://cdn.discordapp.com/avatars/{discordid}/{avatar}.png?size=512' if avatar else f'https://cdn.discordapp.com/embed/avatars/{(int(discordid) >> 22) % 6}.png'
    raise ValueError('Invalid avatar source')


async def save_avatar(app, rid, uid, source, url=None, content=None, expected_source=None):
    lock = app.redis.lock(f'avatar-write:{uid}', timeout=90, blocking=False)
    if not lock.acquire(blocking=False):raise ValueError('Avatar update in progress; try again')
    try:
        await app.db.execute(rid, 'SELECT steamid,discordid FROM user WHERE uid=%s', (uid,))
        identity = await app.db.fetchone(rid)
        if not identity:raise ValueError('User not found')
        if source in ('truckersmp','steam','discord'):
            url = await asyncio.wait_for(provider_url(app, rid, source, *identity), timeout=15)
        await app.db.extend_conn(rid, 60)
        if source != 'upload':content = await fetch_image(url)
        async with _decode_slots:
            png = await asyncio.to_thread(normalize_image, content)
        digest = hashlib.sha256(png).hexdigest()
        base = os.environ.get('HUB_PUBLIC_URL', 'https://'+app.config.domain).rstrip('/')
        avatar = f'{base}{app.config.prefix.rstrip("/")}/avatar/{uid}/{digest}.png'
        await app.db.execute(rid, 'SELECT steamid,discordid FROM user WHERE uid=%s FOR UPDATE', (uid,))
        if await app.db.fetchone(rid) != identity:raise ValueError('Account connection changed; retry')
        if expected_source:
            await app.db.execute(rid, 'SELECT source FROM avatar_profile WHERE uid=%s', (uid,))
            row = await app.db.fetchone(rid)
            if not row or row[0] != expected_source:raise ValueError('Avatar preference changed')
        await app.db.execute(rid, '''INSERT INTO avatar_profile(uid,source,source_url,digest,image,updated_at) VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE source=VALUES(source),source_url=VALUES(source_url),digest=VALUES(digest),image=VALUES(image),updated_at=VALUES(updated_at)''',
            (uid,source,url if source == 'external' else None,digest,png,int(time.time())))
        await app.db.execute(rid, 'UPDATE user SET avatar=%s WHERE uid=%s', (avatar,uid))
        await app.db.commit(rid)
        app.redis.delete(f'uinfo:{uid}')
        return avatar
    except Exception:
        await app.db.execute(rid, 'ROLLBACK')
        raise
    finally:
        if lock.owned():lock.release()


async def refresh_avatar(app, rid, uid):
    await app.db.execute(rid, 'SELECT source,updated_at FROM avatar_profile WHERE uid=%s', (uid,))
    row = await app.db.fetchone(rid)
    if not row or row[0] not in ('truckersmp','steam','discord') or time.time()-row[1]<86400:
        return
    key = f'avatar-refresh:{uid}'
    if not app.redis.set(key, '1', ex=3600, nx=True):return
    try:
        await save_avatar(app, rid, uid, row[0], expected_source=row[0])
    except Exception:
        # Provider failure keeps the last valid avatar.
        pass
