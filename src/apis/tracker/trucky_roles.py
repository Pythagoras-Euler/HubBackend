from fastapi import Header, Request, Response
from functions import auth
from functions.trucky_identity import create_driver_role, forbidden_roles, trainee_role


async def authorize(request, response, authorization):
    await request.app.db.new_conn(request.state.dhrid, db_name=request.app.config.db_name)
    user = await auth(authorization, request, allow_application_token=True, required_permission=["administrator"])
    if user["error"]:
        response.status_code = user.pop("code")
        return user
    return None


async def get_mappings(request: Request, response: Response, authorization: str = Header(None)):
    denied = await authorize(request, response, authorization)
    if denied:
        return denied
    app, rid = request.app, request.state.dhrid
    await app.db.execute(rid, "SELECT companyid,source_roleid,source_name,target_roleid,confirmed FROM trucky_role_mapping ORDER BY confirmed,companyid,source_roleid")
    items = [dict(zip(("companyid", "source_roleid", "source_name", "target_roleid", "confirmed"), row)) for row in await app.db.fetchall(rid)]
    return {"list": items, "roles": [r for r in app.config.roles if r["id"] not in forbidden_roles(app)]}


async def put_mapping(request: Request, response: Response, companyid: int, roleid: int, authorization: str = Header(None)):
    denied = await authorize(request, response, authorization)
    if denied:
        return denied
    app, rid = request.app, request.state.dhrid
    await app.db.execute(rid, "SELECT source_name FROM trucky_role_mapping WHERE companyid=%s AND source_roleid=%s", (companyid, roleid))
    row = await app.db.fetchone(rid)
    if row is None:
        response.status_code = 404
        return {"error": "Role not found"}
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
        action = data.get("action")
        if action == "create":
            target = await create_driver_role(app, str(data.get("name") or row[0]))
        elif action == "trainee":
            target = await trainee_role(app)
        elif action == "map":
            target = int(data["target_roleid"])
            if target not in app.roles or target in forbidden_roles(app):
                raise ValueError("Invalid target role")
        else:
            raise ValueError("Invalid action")
    except (ValueError, TypeError, KeyError) as exc:
        response.status_code = 422
        return {"error": str(exc)}
    await app.db.execute(rid, "UPDATE trucky_role_mapping SET target_roleid=%s,confirmed=1 WHERE companyid=%s AND source_roleid=%s", (target, companyid, roleid))
    await app.db.commit(rid)
    return {"target_roleid": target}
