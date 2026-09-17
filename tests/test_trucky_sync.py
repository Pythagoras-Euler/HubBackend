import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
source = Path(__file__).resolve().parents[1] / 'src/functions/trucky_sync.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
namespace = {'json': json, 'decompress': lambda raw: raw, 'time': time,
             'asyncio': SimpleNamespace(sleep=AsyncMock()), 'genrid': lambda: 'test',
             'Request': lambda **kw: SimpleNamespace(state=SimpleNamespace()),
             'logger': SimpleNamespace(warning=lambda *args: None)}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)


class FakeRedis:
    def __init__(self):
        self.state = {}
        self.snapshots = {}
    def set(self, key, value, **kwargs):
        self.snapshots[key] = json.loads(value)
    def hset(self, key, mapping):
        self.state.update(mapping)


class SyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = SimpleNamespace(redis=FakeRedis(), config=SimpleNamespace(db_name='test'),
            db=SimpleNamespace(new_conn=AsyncMock(), execute=AsyncMock(),
                               fetchall=AsyncMock(return_value=[(10,), (11,)]), close_conn=AsyncMock()))
        self.tracker = {'company_id': 36762, 'api_token': 'private-test-token'}
        self.lock = SimpleNamespace(owned=lambda: True)

    async def run_sync(self, getter, handler):
        namespace['source_json'] = getter
        with patch.dict(sys.modules, {
            'apis.tracker.trucky': SimpleNamespace(convert_format=lambda x: x),
            'functions.tracker': SimpleNamespace(handle_new_job=handler)}):
            await namespace['reconcile'](self.app, self.tracker, self.lock)

    async def test_pagination_skips_existing_and_deleted_and_uses_silent_history(self):
        getter = AsyncMock(side_effect=[
            {'data': [{'id': 10}, {'id': 11}], 'total': 3, 'per_page': 2},
            {'data': [{'id': 12}], 'total': 3, 'per_page': 2},
            {'status': 'completed'}, []])
        handler = AsyncMock(return_value=(7, -1, 2, 12))
        await self.run_sync(getter, handler)
        self.assertEqual(self.app.redis.state['status'], 'complete')
        self.assertEqual(self.app.redis.state['imported'], 1)
        self.assertEqual(self.app.redis.state['existing'], 2)
        handler.assert_awaited_once()
        self.assertTrue(handler.call_args.kwargs['historical'])
        self.assertTrue(handler.call_args.kwargs['allow_external_driver'])
        self.app.db.close_conn.assert_awaited_once()

    async def test_upstream_block_is_visible_and_does_not_mark_success(self):
        handler = AsyncMock()
        await self.run_sync(AsyncMock(side_effect=RuntimeError('Trucky HTTP 403')), handler)
        self.assertEqual(self.app.redis.state['status'], 'failed')
        self.assertEqual(self.app.redis.state['last_error'], 'Trucky HTTP 403')
        self.assertNotIn('last_success_at', self.app.redis.state)
        handler.assert_not_awaited()

    async def test_failed_job_keeps_sweep_partial(self):
        getter = AsyncMock(side_effect=[{'data': [{'id': 12}], 'total': 1, 'per_page': 1},
                                       {'status': 'completed'}, []])
        await self.run_sync(getter, AsyncMock(side_effect=ValueError('bad job')))
        self.assertEqual(self.app.redis.state['status'], 'partial')
        self.assertEqual(self.app.redis.state['failed'], 1)
        self.assertNotIn('last_success_at', self.app.redis.state)

    async def test_empty_intermediate_page_is_failure(self):
        await self.run_sync(AsyncMock(return_value={'data': [], 'total': 100, 'per_page': 10}), AsyncMock())
        self.assertEqual(self.app.redis.state['status'], 'failed')

    async def test_active_jobs_do_not_enter_completed_import(self):
        getter = AsyncMock(side_effect=[
            {'data': [{'id': 12, 'status': 'in_progress'}], 'total': 1, 'per_page': 10},
            {'id': 12, 'status': 'in_progress', 'driver': {'name': 'Test'}}])
        handler = AsyncMock()
        await self.run_sync(getter, handler)
        handler.assert_not_awaited()
        snapshot = self.app.redis.snapshots['trucky-active:36762']
        self.assertEqual(snapshot['list'][0]['status'], 'in_progress')
        self.assertEqual(snapshot['list'][0]['trackerid'], 12)

    async def test_failed_refresh_does_not_replace_live_snapshot(self):
        self.app.redis.snapshots['trucky-active:36762'] = {'list': [{'trackerid': 12}]}
        await self.run_sync(AsyncMock(side_effect=RuntimeError('Trucky HTTP 503')), AsyncMock())
        self.assertEqual(self.app.redis.snapshots['trucky-active:36762']['list'], [{'trackerid': 12}])

    def test_external_drivers_are_separate_and_include_both_games(self):
        def row(lid, steam, unit):
            raw = json.dumps({'data': {'object': {'driver': {'steam_id': steam, 'username': steam}}}})
            return (lid, -1, raw, unit, 100)
        actual = namespace['driver_totals']([row(1, 'a', 1), row(2, 'a', 2), row(3, 'b', 1)])
        self.assertEqual(len(actual), 2)
        self.assertEqual((actual[0]['ets2_jobs'], actual[0]['ats_jobs'], actual[0]['distance']), (1, 1, 200))


if __name__ == '__main__':
    unittest.main()
