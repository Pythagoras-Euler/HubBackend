import ast
import asyncio
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock

source = Path(__file__).resolve().parents[1] / "src/functions/trucky_identity.py"
nodes = [n for n in ast.parse(source.read_text(encoding="utf-8")).body if not isinstance(n, (ast.Import, ast.ImportFrom))]


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns = dict(asyncio=asyncio, copy=copy, json=json, os=os, Path=Path, time=time,
                       str2list=lambda s: [int(v) for v in s.split(',') if v], decompress=lambda s: s,
                       GetUserInfo=AsyncMock())
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.ns)
        self.ns['trainee_role'] = AsyncMock(return_value=10)
        self.app = SimpleNamespace(roles={10: {}, 20: {}, 99: {}}, config_dict={'perms': {'administrator': [99], 'driver': [10, 20]}},
                                   db=SimpleNamespace(execute=AsyncMock(), fetchone=AsyncMock(), fetchall=AsyncMock(return_value=[]), commit=AsyncMock()))
        self.request = SimpleNamespace(app=self.app, state=SimpleNamespace(dhrid='test'))
        self.tracker = {'company_id': 5}

    def test_role_mapping_excludes_admin_and_ambiguous_names(self):
        roles = [{'id': 99, 'name': 'Owner'}, {'id': 20, 'name': ' Driver '}]
        self.assertIsNone(self.ns['same_name_role'](roles, 'owner', {99}))
        self.assertEqual(self.ns['same_name_role'](roles, 'driver', {99}), 20)
        roles.append({'id': 21, 'name': 'Driver'})
        self.assertIsNone(self.ns['same_name_role'](roles, 'driver', {99}))

    def test_only_owned_roles_are_removed(self):
        self.assertEqual(self.ns['merge_roles']([10, 99], [10], [20]), ([20, 99], [20]))
        self.assertEqual(self.ns['merge_roles']([20, 99], [], [20]), ([20, 99], []))

    async def test_first_login_gets_trainee_and_claims_only_matching_steam_history(self):
        self.app.db.fetchone.side_effect = [(-1, 76561198000000001, ',99,'), None, None, ('7',)]
        raw = lambda steam: json.dumps({'data': {'object': {'driver': {'steam_id': steam}}}})
        self.app.db.fetchall.return_value = [(1, raw('76561198000000001')), (2, raw('76561198000000002'))]
        await self.ns['sync_user'](self.request, 3, self.tracker, {'id': 8, 'role_id': 4}, first_login=True)
        calls = self.app.db.execute.call_args_list
        role_write = next(c for c in calls if c.args[1].startswith('UPDATE user SET roles='))
        self.assertEqual(role_write.args[2][0], ',10,99,')
        claims = [c for c in calls if c.args[1].startswith('UPDATE dlog SET userid=')]
        self.assertEqual([c.args[2] for c in claims], [(7, 1)])
        self.assertFalse(any('SELECT target_roleid' in c.args[1] for c in calls))

    async def test_existing_login_keeps_mapping_and_manual_admin(self):
        self.app.db.fetchone.side_effect = [(7, 76561198000000001, ',10,99,'), (7, '10', 1), None, (20,)]
        await self.ns['sync_user'](self.request, 3, self.tracker, {'id': 8, 'role_id': 4}, first_login=True)
        role_write = next(c for c in self.app.db.execute.call_args_list if c.args[1].startswith('UPDATE user SET roles='))
        self.assertEqual(role_write.args[2][0], ',20,99,')

    async def test_leave_removes_synced_role_but_preserves_manual_membership(self):
        self.app.db.fetchone.side_effect = [(7, 76561198000000001, ',10,99,'), (7, '10', 1), None]
        await self.ns['sync_user'](self.request, 3, self.tracker, None)
        calls = self.app.db.execute.call_args_list
        self.assertFalse(any('SET userid=-1' in c.args[1] for c in calls))
        self.assertEqual(next(c for c in calls if c.args[1].startswith('UPDATE user SET roles=')).args[2][0], ',99,')

    async def test_banned_account_is_never_promoted(self):
        self.app.db.fetchone.side_effect = [(-1, 76561198000000001, ''), None, (3,)]
        await self.ns['sync_user'](self.request, 3, self.tracker, {'id': 8})
        self.assertFalse(any(c.args[1].startswith(('UPDATE', 'INSERT')) for c in self.app.db.execute.call_args_list))

    async def test_provider_failure_does_not_become_departure(self):
        self.ns['arequests'] = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status_code=503)))
        with self.assertRaises(RuntimeError):
            await self.ns['lookup_member'](self.app, 'r', {'api_token': 'test', 'company_id': 5}, 76561198000000001)


if __name__ == '__main__':
    unittest.main()
