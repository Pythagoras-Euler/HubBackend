"""Explicit public/member ID boundary for legacy business endpoints.

Only documented member-reference fields are converted. Tracker ingress and the
administrator's ID audit APIs are excluded: upstream IDs are not Hub IDs.
"""
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from account_ids import AccountIDs, IDProblem

SCALARS = frozenset(("userid", "after_userid", "from_userid", "to_userid", "staff_userid",
                     "update_staff_userid", "executor_userid", "assigned_userid", "assigneeid", "foruser",
                     "responded_by", "assign_to_userid", "created_by", "attended_by", "voted_by"))
QUERY_SCALARS = SCALARS | {"owner"}
LISTS = frozenset(("userids", "attendees"))
EXCLUDED = ("/tracksim/update", "/trucky/update", "/truckershub/update", "/unitracker/update", "/custom-tracker/update",
            "/admin/account-ids", "/user/account-id")


class Boundary:
    def __init__(self, service):
        self.service = service
        self.incoming, self.outgoing, self.accounts = {}, {}, {}

    async def decode(self, value):
        if value is None or value == "":
            return value
        # Negative numbers denote built-in economy/system actors, never accounts.
        if re.fullmatch(r"-[0-9]+", str(value)):
            return int(value)
        key = str(value).upper()
        if key not in self.incoming:
            self.incoming[key] = await self.service.resolve(str(value), member=True)
        return self.incoming[key]

    async def encode(self, value):
        if value is None or type(value) is not int or value < 0:
            return value
        if value not in self.outgoing:
            row = await self.service.one("SELECT u.uid,a.public_id FROM user u LEFT JOIN account_id a ON a.uid=u.uid WHERE u.userid=%s", (value,))
            self.outgoing[value] = (row[1] or await self.service.ensure(row[0])) if row else None
            if row:
                self.accounts[row[0]] = self.outgoing[value]
        return self.outgoing[value]

    async def prefetch(self, obj):
        members, accounts = set(), set()
        def visit(value):
            if isinstance(value, list):
                for item in value:
                    visit(item)
            elif isinstance(value, dict):
                if "userid" in value and type(value.get("uid")) is int and value["uid"] >= 0:
                    accounts.add(value["uid"])
                for key, item in value.items():
                    if key in ("application", "data"):
                        continue
                    if key in SCALARS and type(item) is int and item >= 0:
                        members.add(item)
                    visit(item)
        visit(obj)
        # Batched lookup keeps member lists/leaderboards from doing N+1 queries.
        for column, values in (("userid", members), ("uid", accounts)):
            if not values:
                continue
            ordered = sorted(values)
            for offset in range(0, len(ordered), 500):
                batch = ordered[offset:offset+500]
                placeholders = ",".join("%s" for _ in batch)
                rows = await self.service.all(f"SELECT u.uid,u.userid,a.public_id FROM user u LEFT JOIN account_id a ON a.uid=u.uid WHERE u.{column} IN ({placeholders})", tuple(batch))
                for uid, userid, public_id in rows:
                    public_id = public_id or await self.service.ensure(uid)
                    self.accounts[uid] = public_id
                    if userid is not None and userid >= 0:
                        self.outgoing[userid] = public_id
                if column == "userid":
                    for value in batch:
                        self.outgoing.setdefault(value, None)

    async def transform(self, obj, inbound=False, opaque=False):
        convert = self.decode if inbound else self.encode
        if isinstance(obj, list):
            return [await self.transform(item, inbound) for item in obj]
        if not isinstance(obj, dict):
            if not inbound and isinstance(obj, str) and re.fullmatch(r"member_[0-9]+", obj):
                value = await self.encode(int(obj[7:]))
                return f"member_{value}" if value else "online"
            if not inbound and isinstance(obj, str):
                target = re.fullmatch(r"((?:purchase|transfer|reassign)-(?:garage-slot|garage|truck|merch)-[^/]+/for-user-)([0-9]+)", obj)
                if target:
                    return target[1] + str(await self.encode(int(target[2])) or "unknown")
            return obj
        result = {}
        for key, value in obj.items():
            message = re.fullmatch(r"\[Message\] (.*) \((-?[0-9]+)\) #([0-9]+)", key) if not inbound else None
            if message:
                author_id = await self.encode(int(message[2]))
                key = f"[Message] {message[1]} ({author_id or '-1'}) #{message[3]}"
            if opaque:
                # Application question/answer names are user content, not an API
                # schema. Only the system-generated author marker above is an ID.
                result[key] = value
                continue
            if key in SCALARS or (key == "owner" and not inbound and type(value) is int):
                result[key] = await convert(value)
            elif key in LISTS or (key == "assign_to" and (obj.get("assign_mode") in (0, 1) or
                  inbound and "assign_mode" not in obj and isinstance(value, list) and any(isinstance(v, str) for v in value))):
                if isinstance(value, list):
                    result[key] = [await self.transform(v, inbound) if isinstance(v, dict) else await convert(v) for v in value]
                elif isinstance(value, str) and value:
                    result[key] = ",".join([str(await convert(v)) for v in value.split(",") if v])
                else:
                    result[key] = value
            elif key == "owner" and isinstance(value, str) and value.startswith("user-"):
                result[key] = "user-" + str(await convert(value[5:]))
            elif key == "application" and not inbound and isinstance(value, dict):
                result[key] = await self.transform(value, opaque=True)
            elif inbound or key == "data":
                # Legacy request member references are top-level fields. Do not
                # reinterpret custom forms, config documents or tracker payloads.
                result[key] = value
            else:
                result[key] = await self.transform(value, inbound)
        if not inbound and "uid" in obj and "userid" in obj and type(obj["uid"]) is int and obj["uid"] >= 0:
            uid = obj["uid"]
            if uid not in self.accounts:
                self.accounts[uid] = await self.service.ensure(uid)
            result["public_user_id"] = self.accounts[uid]
        return result


class AccountIDRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        if self.path.startswith(EXCLUDED):
            if self.path.startswith(("/admin/account-ids", "/user/account-id")):
                async def private_handler(request):
                    response = await original(request)
                    response.headers["cache-control"] = "no-store"
                    return response
                return private_handler
            return original

        async def public_handler(request):
            db, rid = request.app.db, request.state.dhrid
            await db.new_conn(rid, db_name=request.app.config.db_name)
            boundary = Boundary(AccountIDs(db, rid))
            request.state.account_id_boundary = boundary
            try:
                # FastAPI has matched the route, but has not validated int params.
                for key, value in request.path_params.items():
                    if key in SCALARS:
                        request.path_params[key] = await boundary.decode(value)
                pairs = []
                for key, value in request.query_params.multi_items():
                    if key in QUERY_SCALARS and value:
                        value = str(await boundary.decode(value))
                    elif key in LISTS and value:
                        value = ",".join([str(await boundary.decode(v)) for v in value.split(",") if v])
                    pairs.append((key, value))
                request.scope["query_string"] = urlencode(pairs).encode()
                request.__dict__.pop("_query_params", None)
                if request.method in ("POST", "PUT", "PATCH") and request.headers.get("content-type", "").startswith("application/json"):
                    body = await request.body()
                    if body:
                        data = await boundary.transform(json.loads(body), inbound=True)
                        request._body = json.dumps(data).encode()
                        request._json = data
                response = await original(request)
                if response.headers.get("content-type", "").startswith("application/json") and getattr(response, "body", b""):
                    data = json.loads(response.body)
                    await boundary.prefetch(data)
                    data = await boundary.transform(data)
                    response.body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
                    response.headers["content-length"] = str(len(response.body))
                    if boundary.outgoing or boundary.accounts:
                        response.headers["cache-control"] = "no-store"
                # Banner redirects must also use the public ID.
                if response.headers.get("location"):
                    url = urlsplit(response.headers["location"])
                    pairs = [(k, str(await boundary.encode(int(v))) if k == "userid" and v.isdigit() else v)
                             for k, v in parse_qsl(url.query)]
                    response.headers["location"] = urlunsplit((url.scheme, url.netloc, url.path, urlencode(pairs), url.fragment))
                return response
            except IDProblem as exc:
                await db.execute(rid, "ROLLBACK")
                return JSONResponse(dict(error=exc.code), status_code=exc.status)
            except json.JSONDecodeError:
                return JSONResponse(dict(error="invalid_json"), status_code=400)
        return public_handler


def install_openapi(app):
    """Describe the external contract, while legacy handlers still receive ints."""
    original = app.openapi

    def public_schema():
        schema = original()
        for path, operations in schema.get("paths", {}).items():
            if path.startswith(EXCLUDED):
                continue
            for operation in operations.values():
                if not isinstance(operation, dict):
                    continue
                for parameter in operation.get("parameters", []):
                    if parameter.get("name") in QUERY_SCALARS:
                        parameter["schema"] = dict(type="string", pattern=r"^(?:[A-Za-z0-9]{4,6}|-[0-9]+)$")
                        parameter["description"] = "Current public account ID (case insensitive); negative values are built-in system actors. Historical numeric member IDs are not accepted."
        return schema
    app.openapi = public_schema
