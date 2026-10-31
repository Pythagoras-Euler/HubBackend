"""Policy + real InnoDB tests. Integration is opt-in and creates disposable DBs.

HUB_ACCOUNT_ID_TEST_DB=1 python -m unittest discover -s tests -p test_account_ids.py -v
Uses loopback:13316 by default; never reads the application's DB configuration.
"""
import asyncio
import ast
import importlib.util
import os
from pathlib import Path
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from account_ids import AccountIDs, HOLD_SECONDS, IDProblem, denied, normalize, six_months  # noqa: E402


class PolicyTests(unittest.TestCase):
    def test_format_and_normalization(self):
        for value in ("abcd", "A1b2", "123456", "ABCDEF"):
            self.assertEqual(normalize(value), value.upper())
        for value in (None, 1234, "ABC", "ABCDEFG", " ABCD", "ABCD\n", "ＡBCD", "AB_1", "ABſ1", "中文AB"):
            with self.assertRaises(IDProblem):
                normalize(value)

    def test_six_calendar_months_end_of_month_and_leap_year(self):
        for start, end in (("2026-08-31", "2027-02-28"), ("2023-08-31", "2024-02-29"), ("2026-10-30", "2027-04-30")):
            timestamp = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
            self.assertEqual(datetime.fromtimestamp(six_months(timestamp), timezone.utc).strftime("%Y-%m-%d"), end)

    def test_rule_modes(self):
        rules = [(1, "ADMIN", "exact", 1, ""), (2, "GM", "contains", 1, ""), (3, "TEST", "exact", 0, "")]
        self.assertTrue(denied("ADMIN", rules))
        self.assertFalse(denied("ADMIN1", rules))
        self.assertTrue(denied("AGM1", rules))
        self.assertFalse(denied("TEST", rules))


class SQLDB:
    """The same execute/fetch/commit contract as the application's database."""
    def __init__(self, database):
        import pymysql
        self.conn = pymysql.connect(host="127.0.0.1", port=int(os.environ.get("HUB_ACCOUNT_ID_TEST_PORT", "13316")), user="root", password=os.environ.get("HUB_ACCOUNT_ID_TEST_PASSWORD", ""), database=database, autocommit=False)
        self.cur = self.conn.cursor()

    async def new_conn(self, *args, **kwargs):
        return self.conn

    async def execute(self, rid, sql, args=None):
        self.cur.execute(sql, args)

    async def fetchone(self, rid):
        return self.cur.fetchone()

    async def fetchall(self, rid):
        return self.cur.fetchall()

    async def commit(self, rid):
        self.conn.commit()

    def close(self):
        self.conn.rollback()
        self.conn.close()


@unittest.skipUnless(os.environ.get("HUB_ACCOUNT_ID_TEST_DB") == "1", "opt-in local InnoDB test")
class InnoDBTests(unittest.TestCase):
    def setUp(self):
        import pymysql
        from account_id_migration import migrate
        self.name = "hub_account_id_test_" + uuid.uuid4().hex
        self.admin = pymysql.connect(host="127.0.0.1", port=int(os.environ.get("HUB_ACCOUNT_ID_TEST_PORT", "13316")), user="root", password=os.environ.get("HUB_ACCOUNT_ID_TEST_PASSWORD", ""), autocommit=True)
        with self.admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{self.name}`")
        self.db = SQLDB(self.name)
        self.db.cur.execute("CREATE TABLE user(uid INT PRIMARY KEY,userid INT,name TEXT) ENGINE=InnoDB")
        self.db.cur.execute("INSERT INTO user VALUES (1,123,'Alice'),(2,456,'Admin'),(3,-1,'External')")
        self.db.cur.execute("CREATE TABLE dlog(logid INT PRIMARY KEY,userid INT) ENGINE=InnoDB")
        self.db.cur.execute("INSERT INTO dlog VALUES (7,123)")
        self.db.conn.commit()
        self.assertEqual(migrate(self.db.conn, apply=False), dict(accounts=3, apply=False))
        generated = iter("AA01AA02AA03")
        with patch("account_ids.secrets.choice", side_effect=lambda _: next(generated)):
            migrate(self.db.conn, apply=True)
        self.db.cur.execute("SELECT public_id FROM account_id WHERE uid=1")
        self.initial = self.db.cur.fetchone()[0]

    def tearDown(self):
        self.db.close()
        # The name is generated here, never derived from application settings.
        with self.admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE `{self.name}`")
        self.admin.close()

    def call(self, method, *args, now=1800000000):
        return asyncio.run(getattr(AccountIDs(self.db, "test", now), method)(*args))

    def test_migration_is_restartable_and_default_is_four_characters(self):
        from account_id_migration import migrate
        self.assertEqual(len(self.initial), 4)
        migrate(self.db.conn, apply=True)
        self.assertEqual(self.call("status", 1)["public_id"], self.initial)
        self.assertTrue(self.call("status", 1)["can_change"])
        self.db.cur.execute("SELECT COUNT(*) FROM account_id_history")
        self.assertEqual(self.db.cur.fetchone()[0], 5)
        legacy = self.call("history", 2, None, "123")
        self.assertEqual(legacy["list"][0]["source"], "legacy")
        with self.assertRaises(IDProblem):
            self.call("resolve", "123")

    def test_change_cooldown_grant_and_business_relationships(self):
        result = self.call("change", 1, "ab12")
        self.assertEqual(result["public_id"], "AB12")
        self.assertFalse(result["can_change"])
        with self.assertRaisesRegex(IDProblem, "cooldown"):
            self.call("change", 1, "CD34")
        self.db.conn.rollback()
        self.call("grant", 2, 1, "Support request")
        with self.assertRaisesRegex(IDProblem, "invalid_public_id"):
            self.call("change", 1, "BAD!")
        self.assertTrue(self.call("status", 1)["change_credit"])
        result = self.call("change", 1, "CD34")
        self.assertFalse(result["change_credit"])
        self.db.cur.execute("SELECT userid FROM dlog WHERE logid=7")
        self.assertEqual(self.db.cur.fetchone()[0], 123)
        with self.assertRaises(IDProblem):
            self.call("resolve", "AB12")

    def test_72_hours_and_old_links_do_not_resolve(self):
        self.call("change", 1, "NEW1")
        with self.assertRaises(IDProblem):
            self.call("resolve", self.initial)
        self.assertFalse(self.call("available", self.initial, now=1800000000 + HOLD_SECONDS-1)[0])
        self.assertTrue(self.call("available", self.initial, now=1800000000 + HOLD_SECONDS)[0])
        self.call("change", 2, self.initial, now=1800000000 + HOLD_SECONDS)
        self.assertEqual(self.call("resolve", self.initial), 2)
        result = self.call("history", 2, None, self.initial)
        self.assertEqual(result["total_items"], 2)
        self.assertEqual({r["uid"] for r in result["list"]}, {1, 2})

    def test_cooldown_exact_boundary(self):
        result = self.call("change", 1, "DATE1")
        with self.assertRaisesRegex(IDProblem, "cooldown"):
            self.call("change", 1, "DATE2", now=result["next_change_at"]-1)
        self.db.conn.rollback()
        self.assertEqual(self.call("change", 1, "DATE2", now=result["next_change_at"])["public_id"], "DATE2")

    def test_deleted_account_keeps_private_history_and_releases_name(self):
        self.call("retire", 1, 2)
        self.db.cur.execute("DELETE FROM user WHERE uid=1")
        self.db.conn.commit()
        with self.assertRaises(IDProblem):
            self.call("resolve", self.initial)
        self.assertFalse(self.call("available", self.initial)[0])
        self.assertTrue(self.call("available", self.initial, now=1800000000 + HOLD_SECONDS)[0])
        records = self.call("history", 2, 1)
        self.assertTrue(any(row["public_id"] == self.initial for row in records["list"]))
        self.db.cur.execute("SELECT actor_uid FROM account_id_audit WHERE action='account_deleted'")
        self.assertEqual(self.db.cur.fetchone()[0], 2)

    def test_rules_edit_disable_delete_and_preview_without_renaming(self):
        self.call("change", 1, "ADMIN")
        preview = self.call("preview", "admin", "exact")
        self.assertEqual(preview["total_items"], 1)
        self.call("save_rule", 2, "admin", "exact", True, "Staff identity")
        self.assertEqual(self.call("status", 1)["public_id"], "ADMIN")
        with self.assertRaisesRegex(IDProblem, "forbidden"):
            self.call("change", 2, "ADMIN")
        self.db.conn.rollback()
        self.call("save_rule", 2, "gm", "contains", True, "")
        self.assertEqual(self.call("available", "AGM1"), (False, "public_id_forbidden"))
        rule_id = self.call("rules")[1][0]
        self.call("save_rule", 2, "gm", "contains", False, "Disabled", rule_id)
        self.assertTrue(self.call("available", "AGM1")[0])
        self.call("delete_rule", 2, rule_id)
        self.assertEqual(len(self.call("rules")), 1)

    def test_generated_id_obeys_rules_and_collision_retry(self):
        self.db.cur.execute("INSERT INTO user VALUES (4,789,'New')")
        self.db.conn.commit()
        self.call("save_rule", 2, "AAAA", "exact", True, "")
        stream = iter("AAAA" + self.initial + "ZX91")
        with patch("account_ids.secrets.choice", side_effect=lambda _: next(stream)):
            self.assertEqual(self.call("ensure", 4), "ZX91")

    def race(self, jobs):
        self.db.conn.rollback()
        def worker(job):
            db = SQLDB(self.name)
            try:
                return asyncio.run(job(AccountIDs(db, "race", 1800000000)))
            except IDProblem as exc:
                return exc.code
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, jobs))

    def test_two_accounts_cannot_claim_the_same_id(self):
        result = self.race([lambda s: s.change(1, "RACE"), lambda s: s.change(2, "RACE")])
        self.assertEqual(sum(isinstance(r, dict) for r in result), 1)
        self.assertIn("public_id_unavailable", result)

    def test_duplicate_submission_cannot_bypass_cooldown(self):
        result = self.race([lambda s: s.change(1, "RAC1"), lambda s: s.change(1, "RAC2")])
        self.assertEqual(sum(isinstance(r, dict) for r in result), 1)
        self.assertIn("public_id_cooldown", result)

    def test_cli_creation_assigns_id_and_rolls_back_on_allocation_failure(self):
        import setup
        from unittest.mock import AsyncMock
        self.db.cur.execute("ALTER TABLE user MODIFY uid INT AUTO_INCREMENT")
        for column in ("email", "avatar", "bio", "roles", "discordid", "steamid",
                       "truckersmpid", "join_timestamp", "mfa_secret", "tracker_in_use"):
            self.db.cur.execute(f"ALTER TABLE user ADD COLUMN {column} TEXT")
        self.db.cur.execute("CREATE TABLE user_password(uid INT,email TEXT,password TEXT) ENGINE=InnoDB")
        self.db.conn.commit()
        with patch.object(setup, "genconn", side_effect=lambda _: SQLDB(self.name).conn):
            uid = setup.create_user(None, "cli@example.test", "test-password")
            self.assertIsInstance(uid, int)
            self.db.cur.execute("SELECT a.public_id FROM account_id a JOIN user_password p ON p.uid=a.uid WHERE a.uid=%s", (uid,))
            self.assertIsNotNone(self.db.cur.fetchone())
            self.db.conn.commit()
            with patch.object(AccountIDs, "ensure", new=AsyncMock(side_effect=IDProblem("public_id_generation_exhausted", 503))):
                result = setup.create_user(None, "failed@example.test", "test-password")
            self.assertIsInstance(result, IDProblem)
            for table in ("user", "user_password"):
                self.db.cur.execute(f"SELECT COUNT(*) FROM {table} WHERE email='failed@example.test'")
                self.assertEqual(self.db.cur.fetchone()[0], 0)

    def test_api_boundary_and_admin_permissions(self):
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient
        from account_id_transport import AccountIDRoute
        spec = importlib.util.spec_from_file_location("account_id_endpoint", SRC / "apis/user/account_id.py")
        endpoint = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(endpoint)
        app = FastAPI()
        app.router.route_class = AccountIDRoute
        app.config = SimpleNamespace(db_name=self.name)
        app.db = self.db
        app.redis = SimpleNamespace(delete=lambda *_: None)
        app.config_dict = {"perms": {"administrator": [2], "view_account_id_history": [10], "manage_account_ids": [20], "manage_account_id_rules": [30]}}
        @app.middleware("http")
        async def context(request, call_next):
            request.state.dhrid = "api"
            try:
                return await call_next(request)
            finally:
                self.db.conn.rollback()
        seen = {}
        async def profile(request: Request, userid: int, owner: int | None = None, responded_by: int | None = None, assign_to_userid: int | None = None):
            seen.update(owner=owner, responded_by=responded_by, assign_to_userid=assign_to_userid)
            return dict(uid=1, userid=userid, activity=dict(status=f"member_{userid}"),
                        application={"[Message] Alice (123) #1": "Hello", "userid": "A custom answer"},
                        data=dict(userid=987654),
                        transaction_message="purchase-garage-slot-7/for-user-123")
        async def mutate(request: Request, userid: int):
            return dict(userid=userid, payload=await request.json())
        async def list_filter(userids: str):
            seen["userids"] = userids
            return dict(ok=True)
        app.add_api_route("/test/profile", profile)
        app.add_api_route("/test/list", list_filter)
        app.add_api_route("/member/{userid}/roles", mutate, methods=["PATCH"])
        for route in endpoint.routes():
            app.add_api_route(route.path, route.endpoint, methods=route.methods)
        actor = {"uid": 1, "roles": [], "error": False}
        async def auth(*args, **kwargs):
            return actor
        async def limit(*args, **kwargs):
            return False, {}
        namespace = {}
        tree = ast.parse((SRC / "functions/security.py").read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "checkPerm")
        exec(compile(ast.Module(body=[node], type_ignores=[]), "checkPerm", "exec"), namespace)
        security = SimpleNamespace(auth=auth, ratelimit=limit, checkPerm=namespace["checkPerm"])
        with patch.dict(sys.modules, {"functions.security": security}), TestClient(app) as client:
            response = client.get("/test/list", params={"userids": f"{self.initial.lower()},,AA02"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(seen.pop("userids"), "123,456")
            from account_id_transport import Boundary
            converted = asyncio.run(Boundary(AccountIDs(self.db, "test")).transform(
                {"userids": f"{self.initial.lower()},,AA02"}, inbound=True))
            self.assertEqual(converted["userids"], "123,456")
            response = client.get(f"/test/profile?userid={self.initial.lower()}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["userid"], self.initial)
            self.assertEqual(response.json()["activity"]["status"], f"member_{self.initial}")
            self.assertIn(f"[Message] Alice ({self.initial}) #1", response.json()["application"])
            self.assertEqual(response.json()["application"]["userid"], "A custom answer")
            self.assertEqual(response.json()["data"]["userid"], 987654)
            self.assertEqual(response.json()["transaction_message"], f"purchase-garage-slot-7/for-user-{self.initial}")
            self.assertEqual(response.headers["cache-control"], "no-store")
            response = client.get(f"/test/profile?userid={self.initial}&owner={self.initial}&responded_by={self.initial}&assign_to_userid={self.initial}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(seen, dict(owner=123, responded_by=123, assign_to_userid=123))
            self.assertEqual(client.get("/test/profile?userid=123").status_code, 400)
            response = client.patch(f"/member/{self.initial}/roles", json={"assign_mode": 1, "assign_to": [self.initial], "roles": [123], "to_userid": self.initial})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["payload"]["roles"], [123])
            self.assertEqual(response.json()["payload"]["assign_to"], [self.initial])
            for path in ("history", "audit", "rules", "status?uid=1"):
                self.assertEqual(client.get(f"/admin/account-ids/{path}").status_code, 403)
            self.assertEqual(client.post("/admin/account-ids/credit", json={"uid": 1, "reason": "test"}).status_code, 403)
            response = client.patch("/user/account-id", json={"public_id": "API1", "uid": 2})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.call("resolve", "API1"), 1)
            self.assertEqual(client.get(f"/test/profile?userid={self.initial}").status_code, 404)
            actor["roles"] = [30]
            self.assertEqual(client.get("/admin/account-ids/rules").status_code, 200)
            self.assertEqual(client.get("/admin/account-ids/history").status_code, 403)
            self.assertEqual(client.post("/admin/account-ids/credit", json={"uid": 1, "reason": "test"}).status_code, 403)
            actor["roles"] = [10]
            self.assertEqual(client.get("/admin/account-ids/history").status_code, 200)
            self.assertEqual(client.get("/admin/account-ids/rules").status_code, 403)
            actor["roles"] = [2]
            history = client.get(f"/admin/account-ids/history?uid=1&public_id={self.initial}")
            self.assertEqual(history.status_code, 200, history.text)
            self.assertEqual(history.json()["total_items"], 1)
            audit = client.get("/admin/account-ids/audit?uid=1")
            self.assertTrue(any(r["action"] == "view_history" for r in audit.json()["list"]))


if __name__ == "__main__":
    unittest.main()
