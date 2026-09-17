"""Steam-keyed Trucky membership and explicitly owned role synchronization."""
import asyncio
import copy
import json
import os
from pathlib import Path
import time

from functions.arequests import arequests
from functions.dataop import decompress, str2list
from functions.general import Dict2Obj
from functions.userinfo import GetUserInfo
from logger import logger

_config_lock = asyncio.Lock()


def same_name_role(roles, name, forbidden):
    matches = [r["id"] for r in roles if r["name"].strip().casefold() == name.strip().casefold()
               and r["id"] not in forbidden]
    return matches[0] if len(matches) == 1 else None


def merge_roles(current, previously_owned, desired):
    """Only remove roles this integration originally added, never manual roles."""
    current, previous, desired = set(current), set(previously_owned), set(desired)
    manual = current - previous
    return sorted(manual | desired), sorted(desired - manual)


def forbidden_roles(app):
    return {0, *app.config_dict["perms"].get("administrator", [])}


async def create_driver_role(app, name):
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError("Invalid role name")
    async with _config_lock:
        lock = app.redis.lock(f"trucky-config:{app.config.abbr}", timeout=20, blocking=False)
        if not lock.acquire(blocking=False):
            raise ValueError("Configuration is busy; retry shortly")
        try:
            path = Path(app.config_path)
            if Path(str(path) + ".saved").exists():
                raise ValueError("Apply pending configuration changes first")
            config = json.loads(path.read_text(encoding="utf-8"))
            role_id = same_name_role(config["roles"], name, forbidden_roles(app))
            if role_id is not None:
                return role_id
            if any(r["name"].strip().casefold() == name.casefold() for r in config["roles"]):
                raise ValueError("Role name is reserved")
            role_id = max(r["id"] for r in config["roles"]) + 1
            order = max(r["order_id"] for r in config["roles"]) + 10
            config["roles"].append({"id": role_id, "order_id": order, "name": name})
            config["perms"]["driver"].append(role_id)
            temporary = Path(str(path) + ".trucky-tmp")
            temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.chmod(0o600)
            os.replace(temporary, path)
            app.config_dict = config
            app.config = Dict2Obj(config)
            app.backup_config = copy.deepcopy(config)
            app.config_last_modified = path.stat().st_mtime
            import static
            static.load(app)
            return role_id
        finally:
            if lock.owned():
                lock.release()


async def trainee_role(app):
    existing = same_name_role(app.config.roles, "实习司机", forbidden_roles(app))
    if existing is not None:
        return existing
    return await create_driver_role(app, "实习司机")


async def lookup_member(app, rid, tracker, steamid):
    result = await arequests.get(app, f"https://e.truckyapp.com/api/v1/user/steam/{int(steamid)}",
                                headers={"X-ACCESS-TOKEN": tracker["api_token"], "Accept": "application/json"}, dhrid=rid)
    if result.status_code == 404:
        return None
    if result.status_code != 200:
        raise RuntimeError(f"Trucky identity HTTP {result.status_code}")
    member = result.json()
    if not isinstance(member, dict) or not member.get("id") or "company_id" not in member:
        raise ValueError("Unexpected Trucky identity response")
    return member if str(member["company_id"]) == str(tracker["company_id"]) else None


async def refresh_roles(app, rid, tracker):
    from functions.trucky_sync import source_json
    roles = await source_json(app, rid, f"company/{int(tracker['company_id'])}/roles", tracker["api_token"])
    if not isinstance(roles, list):
        raise ValueError("Unexpected Trucky roles response")
    for role in roles:
        target = same_name_role(app.config.roles, role["name"], forbidden_roles(app))
        await app.db.execute(rid, "INSERT INTO trucky_role_mapping(companyid,source_roleid,source_name,target_roleid,confirmed) VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE source_name=VALUES(source_name)",
                             (int(tracker["company_id"]), int(role["id"]), role["name"], target, int(target is not None)))
        if target is not None:
            await app.db.execute(rid, "UPDATE trucky_role_mapping SET target_roleid=%s,confirmed=1 WHERE companyid=%s AND source_roleid=%s AND confirmed=0",
                                 (target, int(tracker["company_id"]), int(role["id"])))
    await app.db.commit(rid)


async def sync_user(request, uid, tracker, member, first_login=False):
    app, rid = request.app, request.state.dhrid
    company = int(tracker["company_id"])
    await app.db.execute(rid, "SELECT userid,steamid,roles FROM user WHERE uid=%s FOR UPDATE", (uid,))
    row = await app.db.fetchone(rid)
    if row is None:
        return
    userid, steamid, roles = row
    await app.db.execute(rid, "SELECT assigned_userid,managed_roles,auto_admitted FROM trucky_identity WHERE uid=%s AND companyid=%s FOR UPDATE", (uid, company))
    previous = await app.db.fetchone(rid)
    if member is None and previous is None:
        await app.db.commit(rid)
        return
    await app.db.execute(rid, "SELECT uid FROM banned WHERE (uid=%s OR steamid=%s) AND expire_timestamp>%s UNION SELECT uid FROM pending_user_deletion WHERE uid=%s", (uid, steamid, int(time.time()), uid))
    if await app.db.fetchone(rid):
        await app.db.commit(rid)
        return
    owned = str2list(previous[1]) if previous else []
    auto_admitted = previous[2] if previous else 0
    assigned = previous[0] if previous else userid
    desired = []
    if member is not None:
        default_role = await trainee_role(app)
        role_id = member.get("role_id") or (member.get("role") or {}).get("id")
        target = None
        initial_admission = first_login and previous is None and (userid is None or userid < 0)
        if role_id is not None and not initial_admission:
            await app.db.execute(rid, "SELECT target_roleid FROM trucky_role_mapping WHERE companyid=%s AND source_roleid=%s AND confirmed=1", (company, int(role_id)))
            mapping = await app.db.fetchone(rid)
            target = mapping[0] if mapping else None
        if target not in app.roles or target in forbidden_roles(app):
            target = default_role
        desired = [target]
        if target not in app.config_dict["perms"].get("driver", []):
            desired.append(default_role)
        if userid is None or userid < 0:
            if assigned is None or assigned < 0:
                await app.db.execute(rid, "SELECT sval FROM settings WHERE skey='nxtuserid' FOR UPDATE")
                assigned = int((await app.db.fetchone(rid))[0])
                await app.db.execute(rid, "UPDATE settings SET sval=%s WHERE skey='nxtuserid'", (str(assigned+1),))
            userid = assigned
            auto_admitted = 1
            await app.db.execute(rid, "UPDATE user SET userid=%s,join_timestamp=%s,tracker_in_use=3 WHERE uid=%s", (userid, int(time.time()), uid))
    updated, managed = merge_roles(str2list(roles), owned, desired)
    old_roles = set(str2list(roles))
    if set(updated) != old_roles:
        await app.db.execute(rid, "INSERT INTO user_role_history(uid,added_roles,removed_roles,timestamp) VALUES (%s,%s,%s,%s)",
                             (uid, ",".join(map(str, sorted(set(updated)-old_roles))), ",".join(map(str, sorted(old_roles-set(updated)))), int(time.time())))
    if member is None and auto_admitted and not updated:
        await app.db.execute(rid, "UPDATE user SET userid=-1 WHERE uid=%s", (uid,))
    await app.db.execute(rid, "UPDATE user SET roles=%s WHERE uid=%s", (","+",".join(map(str, updated))+",", uid))
    await app.db.execute(rid, "INSERT INTO trucky_identity(uid,companyid,source_userid,assigned_userid,managed_roles,auto_admitted,is_member,checked_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE source_userid=VALUES(source_userid),assigned_userid=VALUES(assigned_userid),managed_roles=VALUES(managed_roles),auto_admitted=VALUES(auto_admitted),is_member=VALUES(is_member),checked_at=VALUES(checked_at)",
                         (uid, company, member["id"] if member else None, assigned, ",".join(map(str, managed)), auto_admitted, int(member is not None), int(time.time())))
    if member is not None and userid is not None and userid >= 0:
        await app.db.execute(rid, "SELECT logid,data FROM dlog WHERE tracker_type=3 AND userid=-1")
        for logid, raw in await app.db.fetchall(rid):
            driver = json.loads(decompress(raw))["data"]["object"]["driver"]
            if str(driver["steam_id"]) == str(steamid):
                await app.db.execute(rid, "UPDATE dlog SET userid=%s WHERE logid=%s AND userid=-1", (userid, logid))
    await app.db.commit(rid)
    await GetUserInfo(request, uid=uid, nocache=True)


async def sync_login(request, uid, steamid):
    for tracker in request.app.config.trackers:
        if tracker["type"] != "trucky" or not tracker.get("api_token") or not tracker.get("company_id"):
            continue
        try:
            member = await lookup_member(request.app, request.state.dhrid, tracker, steamid)
            if member is not None:
                await sync_user(request, uid, tracker, member, first_login=True)
                return
        except Exception as exc:
            await request.app.db.execute(request.state.dhrid, "ROLLBACK")
            logger.warning("Trucky login sync deferred: %s", type(exc).__name__)


async def sync_company(request, tracker):
    app, rid = request.app, request.state.dhrid
    await refresh_roles(app, rid, tracker)
    await app.db.execute(rid, "SELECT uid,steamid FROM user WHERE steamid IS NOT NULL")
    users = await app.db.fetchall(rid)
    for uid, steamid in users:
        member = await lookup_member(app, rid, tracker, steamid)
        await sync_user(request, uid, tracker, member)
        await asyncio.sleep(1.1)
