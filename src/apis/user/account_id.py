"""Account ID operations; private history/rules never use the public adapter."""
import json
from typing import Optional

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from account_ids import AccountIDs, IDProblem


async def handle(request: Request, authorization: Optional[str] = Header(None)):
    # Import lazily, as security also imports userinfo through functions.
    from functions.security import auth, ratelimit, checkPerm
    app, rid = request.app, request.state.dhrid
    path = request.scope["route"].path
    admin = path.startswith("/admin/account-ids")
    rl = await ratelimit(request, "account-ids", 60, 30)
    if rl[0]:
        return rl[1]
    await app.db.new_conn(rid, db_name=app.config.db_name)
    au = await auth(authorization, request, check_member=False)
    if au["error"]:
        return JSONResponse({"error": au["error"]}, status_code=au["code"])
    if admin:
        permission = ("manage_account_id_rules" if path.endswith(("/rules", "/preview")) else
                      "manage_account_ids" if path.endswith(("/credit", "/status")) else "view_account_id_history")
        if not checkPerm(app, au["roles"], ["administrator", permission]):
            return JSONResponse(dict(error="forbidden"), status_code=403)
    service = AccountIDs(app.db, rid)
    method, params = request.method, request.query_params
    try:
        body = await request.json() if method in ("POST", "PUT", "PATCH") else {}
        if not isinstance(body, dict):
            raise IDProblem("invalid_body")
        if not admin:
            if path.endswith("/availability"):
                ok, error = await service.available(params.get("public_id"))
                return dict(available=ok, reason=error)
            if method == "PATCH":
                result = await service.change(au["uid"], body.get("public_id"))
                # Also invalidate ordinary profiles on the browser through reload.
                app.redis.delete(f"uinfo:{au['uid']}")
                return result
            return await service.status(au["uid"])
        if path.endswith("/history"):
            uid = int(params["uid"]) if params.get("uid") else None
            return await service.history(au["uid"], uid, params.get("public_id"), int(params.get("page", 1)))
        if path.endswith("/audit"):
            uid = int(params["uid"]) if params.get("uid") else None
            page = int(params.get("page", 1))
            if page < 1:
                raise IDProblem("invalid_page")
            where, args = ("target_uid=%s", [uid]) if uid is not None else ("1=1", [])
            total = await service.one(f"SELECT COUNT(*) FROM account_id_audit WHERE {where}", tuple(args))
            rows = await service.all(f"SELECT id,actor_uid,target_uid,action,detail,timestamp FROM account_id_audit WHERE {where} ORDER BY id DESC LIMIT 50 OFFSET %s", tuple(args + [(page-1)*50]))
            await service.audit(au["uid"], uid, "view_audit", dict(page=page))
            await app.db.commit(rid)
            return dict(list=[dict(id=r[0], actor_uid=r[1], target_uid=r[2], action=r[3], detail=json.loads(r[4]), timestamp=r[5]) for r in rows], total_items=total[0])
        if path.endswith("/credit"):
            return await service.grant(au["uid"], int(body["uid"]), body.get("reason"))
        if path.endswith("/preview"):
            return await service.preview(body.get("word"), body.get("mode", "exact"))
        if path.endswith("/status"):
            return await service.status(int(params["uid"]))
        if path.endswith("/rules"):
            if method == "GET":
                return dict(list=[dict(zip(("id", "word", "mode", "enabled", "note"), r)) for r in await service.rules()])
            if method == "DELETE":
                await service.delete_rule(au["uid"], int(params["id"]))
            else:
                rule_id = body.get("id")
                if rule_id is not None and (type(rule_id) is not int or rule_id < 1):
                    raise IDProblem("invalid_rule")
                await service.save_rule(au["uid"], body.get("word"), body.get("mode", "exact"), body.get("enabled", True), body.get("note", ""), rule_id)
            return dict(ok=True)
        raise IDProblem("not_found", 404)
    except IDProblem as exc:
        await app.db.execute(rid, "ROLLBACK")
        return JSONResponse(dict(error=exc.code), status_code=exc.status)
    except (ValueError, TypeError, KeyError):
        await app.db.execute(rid, "ROLLBACK")
        return JSONResponse(dict(error="invalid_request"), status_code=400)


def routes():
    from fastapi.routing import APIRoute
    definitions = (
        ("/user/account-id", ["GET", "PATCH"]),
        ("/user/account-id/availability", ["GET"]),
        ("/admin/account-ids/status", ["GET"]),
        ("/admin/account-ids/history", ["GET"]),
        ("/admin/account-ids/audit", ["GET"]),
        ("/admin/account-ids/credit", ["POST"]),
        ("/admin/account-ids/rules", ["GET", "PUT", "DELETE"]),
        ("/admin/account-ids/preview", ["POST"]),
    )
    return [APIRoute(path, handle, methods=methods) for path, methods in definitions]
