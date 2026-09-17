import ast
import asyncio
import math
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock

source = Path(__file__).resolve().parents[1] / "src/apis/map_info.py"
nodes = [n for n in ast.parse(source.read_text(encoding="utf-8")).body
         if not isinstance(n, (ast.Import, ast.ImportFrom))]


class MapInfoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.metadata = dict(x1=-100, x2=100, y1=-200, y2=200, minZoom=0, maxZoom=8)
        self.fetch = AsyncMock(return_value=SimpleNamespace(status_code=200, json=lambda: self.metadata))
        self.ns = dict(asyncio=asyncio, math=math, time=time, Request=object, Response=object,
                       arequests=SimpleNamespace(get=self.fetch))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.ns)
        self.response = SimpleNamespace(status_code=200, headers={})

    async def test_only_allowlisted_maps_can_trigger_upstream_request(self):
        for game, variant in [("http:", "internal"), ("ats", "promods-classic"), ("ets2", "../secret")]:
            result = await self.ns["get_map_info"](None, self.response, game, variant)
            self.assertEqual(self.response.status_code, 404)
            self.assertIn("error", result)
        self.fetch.assert_not_awaited()

    async def test_concurrent_requests_share_one_cached_validated_result(self):
        result = await asyncio.gather(*[self.ns["get_map_info"](None, self.response, "ets2", "base") for _ in range(5)])
        self.assertTrue(all(r == self.metadata for r in result))
        self.fetch.assert_awaited_once()
        self.assertEqual(self.fetch.call_args.args[1], "https://map.charlws.com/ets2/base/info/TileMapInfo.json")
        self.assertEqual(self.response.headers["Cache-Control"], "public, max-age=3600")

    async def test_upstream_failure_is_not_cached_as_success(self):
        self.fetch.side_effect = TimeoutError()
        result = await self.ns["get_map_info"](None, self.response, "ats", "base")
        self.assertEqual(self.response.status_code, 502)
        self.assertIn("error", result)
        self.assertEqual(self.ns["_cache"], {})

    def test_rejects_nonfinite_or_reversed_bounds(self):
        for changes in [dict(x1=float("nan")), dict(y2=-300), dict(minZoom=-1), dict(maxZoom=99), dict(x1=True)]:
            with self.assertRaises(ValueError):
                self.ns["validate_metadata"]({**self.metadata, **changes})


if __name__ == "__main__":
    unittest.main()
