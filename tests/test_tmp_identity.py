import ast
import asyncio
import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
from truckersmp_identity import verified_player

STEAM = '76561198000000001'


def payload(**fields):
    return {'error':False, 'response':dict(id=42, steamID64=STEAM, **fields)}


class IdentityTests(unittest.TestCase):
    def test_same_steam_only(self):
        self.assertEqual(verified_player(payload(), STEAM)['id'], 42)
        self.assertEqual(verified_player(payload(), int(STEAM))['id'], 42)
        self.assertIsNone(verified_player(payload(), '76561198000000002'))

    def test_malformed_identity_rejected(self):
        for value in (None, {}, {'error':True,'response':payload()['response']},
                      {'error':False,'response':{'id':True,'steamID64':STEAM}},
                      {'error':False,'response':{'id':0,'steamID64':STEAM}}):
            self.assertIsNone(verified_player(value, STEAM))


class SyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse((SRC/'functions/truckersmp_identity.py').read_text(encoding='utf-8'))
        self.api = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status_code=200,json=lambda:payload())))
        ns = dict(asyncio=asyncio, arequests=self.api, verified_player=verified_player, logger=logging.getLogger('test'))
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name=='sync_user'], type_ignores=[]), 'tmp_sync', 'exec'), ns)
        self.sync = ns['sync_user']
        self.app = SimpleNamespace(db=SimpleNamespace(execute=AsyncMock(),commit=AsyncMock()), redis=Mock())
        self.app.redis.get.return_value = None
        self.app.redis.lock.return_value.acquire.return_value = True

    async def test_verified_binding_uses_steam_guard_and_invalidates_cache(self):
        self.assertEqual(await self.sync(self.app, 'r', 6, STEAM), 42)
        sql,args = self.app.db.execute.await_args.args[1:]
        self.assertIn('AND steamid=%s', sql)
        self.assertEqual(args, (42,6,STEAM))
        self.app.redis.delete.assert_called_with('uinfo:6')
        self.app.db.commit.assert_awaited_once()

    async def test_provider_failure_preserves_binding(self):
        self.api.get.return_value.status_code = 503
        self.assertIsNone(await self.sync(self.app, 'r', 6, STEAM))
        self.app.db.execute.assert_not_awaited()

    async def test_mismatched_steam_never_binds(self):
        self.api.get.return_value.json = lambda:payload()
        self.assertIsNone(await self.sync(self.app, 'r', 6, '76561198000000002'))
        self.app.db.execute.assert_not_awaited()

    async def test_cache_skips_provider(self):
        self.app.redis.get.return_value = '42'
        self.assertEqual(await self.sync(self.app, 'r', 6, STEAM), 42)
        self.api.get.assert_not_awaited()

    async def test_missing_steam_skips_provider(self):
        self.assertIsNone(await self.sync(self.app, 'r', 6, None))
        self.api.get.assert_not_awaited()
