"""Exercise the real tracker switch handler without external services."""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock


class TrackerSelectionTests(IsolatedAsyncioTestCase):
    async def test_driver_can_save_truckershub_and_cache_uses_its_name(self):
        path = Path(__file__).resolve().parents[1] / 'src/apis/user/info.py'
        node = next(n for n in ast.parse(path.read_text(encoding='utf-8')).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'post_tracker_switch')
        ns = dict(Request=object, Response=lambda **kw: SimpleNamespace(**kw),
                  Optional=Optional, Header=lambda _: None,
                  ratelimit=AsyncMock(return_value=(False, {})),
                  auth=AsyncMock(return_value={'error': False, 'uid': 7, 'language': 'en'}))
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
        db = SimpleNamespace(new_conn=AsyncMock(), execute=AsyncMock(), commit=AsyncMock())
        redis = SimpleNamespace(hset=Mock())
        request = SimpleNamespace(app=SimpleNamespace(db=db, redis=redis,
                                  config=SimpleNamespace(db_name='fixture')),
                                  state=SimpleNamespace(dhrid='fixture'),
                                  json=AsyncMock(return_value={'tracker': 'truckershub'}))
        response = SimpleNamespace(headers={})
        result = await ns['post_tracker_switch'](request, response)
        self.assertEqual(result.status_code, 204)
        self.assertIn('tracker_in_use = 6', db.execute.call_args.args[1])
        db.commit.assert_awaited_once()
        self.assertEqual(redis.hset.call_args.kwargs['mapping']['tracker'], 'truckershub')

