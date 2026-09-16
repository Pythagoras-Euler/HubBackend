"""Route behavior without starting Redis/tracker/background workers."""
import ast
import math
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Optional
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from delivery_filters import metadata_filters
from public_ids import PublicIDs
from public_id_store import resolve_public_id

source = Path(__file__).resolve().parents[1] / "src/apis/dlog/info.py"
nodes = [n for n in ast.parse(source.read_text(encoding="utf-8")).body
         if isinstance(n, ast.AsyncFunctionDef) and n.name in ("get_list", "get_public_dlog", "get_dlog", "delete_dlog")]


class DeliveryAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = SimpleNamespace(new_conn=AsyncMock(), execute=AsyncMock(),
                                  fetchall=AsyncMock(side_effect=[[], [(0,)]]),
                                  fetchone=AsyncMock(return_value=(17,)))
        self.app = SimpleNamespace(db=self.db, config=SimpleNamespace(db_name="test", privacy=False))
        self.request = SimpleNamespace(app=self.app, state=SimpleNamespace(dhrid="r"))
        self.response = SimpleNamespace(status_code=200, headers={})
        self.ns = dict(Request=object, Response=object, Header=lambda _: None, Optional=Optional,
                       math=math, metadata_filters=metadata_filters,
                       ratelimit=AsyncMock(return_value=(False, {})),
                       resolve_public_id=resolve_public_id, get_dlog=AsyncMock(return_value={"ok": True}),
                       ml=SimpleNamespace(tr=lambda *args, **kwargs: "error"))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.ns)
        self.numeric_get = self.ns["get_dlog"]
        self.ns["get_dlog"] = AsyncMock(return_value={"ok": True})

    async def test_default_chronological_sort_and_combined_filters_match_count(self):
        result = await self.ns["get_list"](self.request, self.response, source="London", destination="Paris", cargo="Steel", userid=5)
        queries = self.db.execute.await_args_list
        self.assertEqual(len(queries), 2)
        self.assertIn("ORDER BY dlog.timestamp desc, dlog.logid DESC", queries[0].args[1])
        for call in queries:
            self.assertIn("dlog.userid = 5", call.args[1])
            self.assertIn("EXISTS", call.args[1])
            self.assertEqual(call.args[2], ("London", "London", "Paris", "Paris", "Steel"))
        self.assertEqual(result["total_items"], 0)

    async def test_profit_order_and_reject_invalid_sort(self):
        await self.ns["get_list"](self.request, self.response, order_by="profit", order="asc")
        self.assertIn("ORDER BY dlog.profit asc", self.db.execute.call_args_list[0].args[1])
        self.db.execute.reset_mock()
        await self.ns["get_list"](self.request, self.response, order_by="profit; DROP TABLE dlog")
        self.assertEqual(self.response.status_code, 400)
        self.db.execute.assert_not_awaited()

    async def test_public_lookup_reuses_numeric_authorization_and_404_behavior(self):
        pid = PublicIDs("2020-01", b"api-test-key-at-least-thirty-two-bytes").generate_public_id("2025-09-01")
        await self.ns["get_public_dlog"](self.request, self.response, pid.lower(), "Bearer existing-token")
        self.ns["get_dlog"].assert_awaited_once_with(self.request, self.response, 17, "Bearer existing-token")
        self.db.fetchone.return_value = None
        await self.ns["get_public_dlog"](self.request, self.response, pid, None)
        self.assertIsNone(self.ns["get_dlog"].call_args.args[2])

    async def test_bad_public_id_is_rejected_without_database_query(self):
        await self.ns["get_public_dlog"](self.request, self.response, "invalid", None)
        self.assertEqual(self.response.status_code, 400)
        self.db.execute.assert_not_awaited()
        self.ns["get_dlog"].assert_not_awaited()

    async def test_public_and_numeric_routes_both_preserve_auth_failure(self):
        self.ns["get_dlog"] = self.numeric_get
        self.ns["auth"] = AsyncMock(return_value={"error": "denied", "code": 403})
        pid = PublicIDs("2020-01", b"api-test-key-at-least-thirty-two-bytes").generate_public_id("2025-09-01")
        result = await self.ns["get_public_dlog"](self.request, self.response, pid, "Bearer invalid")
        self.assertEqual((self.response.status_code, result), (403, {"error": "denied"}))
        self.assertTrue(all(not call.args[1].startswith("UPDATE") for call in self.db.execute.call_args_list))

    async def test_numeric_missing_record_still_returns_404(self):
        self.db.fetchall.side_effect = [[]]
        await self.numeric_get(self.request, self.response, 123, None)
        self.assertEqual(self.response.status_code, 404)
        self.assertIn("logid = 123", self.db.execute.call_args.args[1])

    async def test_delete_keeps_permission_check_before_writes(self):
        self.ns["auth"] = AsyncMock(return_value={"error": "denied", "code": 403})
        await self.ns["delete_dlog"](self.request, self.response, 123, "Bearer no-permission")
        self.assertEqual(self.response.status_code, 403)
        self.db.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
