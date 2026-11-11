"""Event response and privacy regressions with isolated database/auth dependencies."""
import ast
import math
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from event_banner import event_banner_url

SOURCE = Path(__file__).resolve().parents[1] / 'src/plugins/event.py'
NAMES = {'get_list', 'get_event', '_normalize_truckersmp_event', '_truckersmp_event_id', '_truckersmp_timestamp', '_truckersmp_location'}
NODES = [n for n in ast.parse(SOURCE.read_text(encoding='utf-8')).body
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in NAMES]


class BannerTests(unittest.TestCase):
    def test_legacy_heading_and_url_validation(self):
        cases = {
            '[Image src="https://static.truckersmp.com/a.png"]\nText': 'https://static.truckersmp.com/a.png',
            '[Image src="http://cdn.example/a.png"]': 'http://cdn.example/a.png',
            '[Image src="/images/a.png"]': 'https://hub.example/images/a.png',
            '[Image src="images/a.png"]': 'https://hub.example/images/a.png',
            '[Image src="//cdn.example/a.png"]': 'https://cdn.example/a.png',
            'Text': None, '': None, None: None,
            'Text [Image src="https://example.com/a.png"]': None,
            '[image src="https://example.com/a.png"]': None,
        }
        for value in ['', 'javascript:alert(1)', 'data:image/png;base64,x', 'ftp://host/a', 'https://', 'https:/a', 'https://[bad/a', 'https://host:bad/a', 'https://user:pass@host/a', '#a', '?a', 'https://host/a b', 'https://host/\\a']:
            cases[f'[Image src="{value}"]'] = None
        with patch.dict(os.environ, {'HUB_PUBLIC_URL': 'https://hub.example'}):
            for description, expected in cases.items():
                with self.subTest(description=description):
                    self.assertEqual(event_banner_url(description, SimpleNamespace(domain='api.example')), expected)

    def test_config_domain_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(event_banner_url('[Image src="/a.png"]', SimpleNamespace(domain='hub.example')), 'https://hub.example/a.png')


class EventAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'HUB_PUBLIC_URL': 'https://hub.example'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = SimpleNamespace(new_conn=AsyncMock(), execute=AsyncMock(), fetchall=AsyncMock())
        self.request = SimpleNamespace(app=SimpleNamespace(db=self.db, config=SimpleNamespace(db_name='test', domain='hub.example')), state=SimpleNamespace(dhrid='test'))
        self.response = SimpleNamespace(status_code=200, headers={})
        self.ns = dict(Request=object, Response=object, Header=lambda _: None, Optional=Optional,
                       math=math, re=re, datetime=datetime, timezone=timezone,
                       TRUCKERSMP_EVENT_LINK_RE=re.compile(r'^https?://(?:www\.)?truckersmp\.com/events/(\d+)(?:[-/].*)?$'),
                       event_banner_url=event_banner_url, decompress=lambda value: value,
                       str2list=lambda value: [int(v) for v in value.split(',') if v], TF={0: False, 1: True},
                       GetUserInfo=AsyncMock(return_value={'userid': 7}), ActivityUpdate=AsyncMock(),
                       ratelimit=AsyncMock(return_value=(False, {})),
                       auth=AsyncMock(return_value={'error': False, 'userid': 7, 'uid': 7, 'language': 'en'}),
                       ml=SimpleNamespace(tr=lambda *a, **k: 'event_not_found'))
        exec(compile(ast.Module(body=NODES, type_ignores=[]), str(SOURCE), 'exec'), self.ns)

    def row(self, description='Text', private=0):
        return (1, '', 'London', 'Paris', '100 km', 1800000000, 1800003600, description, 'Convoy', ',7,', ',7,', private, 0, 0, 0, 1700000000, 7)

    async def test_list_and_detail_share_banner_without_changing_history(self):
        for description, expected in [('[Image src="/images/old.png"]\nOld event', 'https://hub.example/images/old.png'), ('Text', None), ('[Image src="javascript:x"]', None)]:
            with self.subTest(description=description):
                self.db.fetchall.side_effect = [[(1,)], [self.row(description)]]
                result = await self.ns['get_list'](self.request, self.response)
                item = result['list'][0]
                self.assertEqual(item['banner_url'], expected)
                self.assertEqual(item['description'], description)
                self.assertEqual((item['attendees'], item['votes'], item['voted']), (0, 0, None))
                self.db.fetchall.side_effect = [[self.row(description)]]
                detail = await self.ns['get_event'](self.request, self.response, 1)
                self.assertEqual(detail['banner_url'], expected)
                self.assertEqual((detail['attendees'], detail['votes']), ([], []))
        self.ns['auth'].assert_not_awaited()

    async def test_anonymous_private_filter_cannot_override_public_boundary(self):
        self.db.fetchall.side_effect = [[]]
        result = await self.ns['get_list'](self.request, self.response, is_private=True)
        query = self.db.execute.call_args.args[1]
        self.assertIn('AND is_private = 0', query)
        self.assertIn('AND is_private = 1', query)
        self.assertEqual(result['list'], [])

    async def test_pagination_time_and_sort_filters_preserved(self):
        self.db.fetchall.side_effect = [[(1,), (2,), (3,)], [self.row()]]
        result = await self.ns['get_list'](self.request, self.response, page=2, page_size=1, meetup_after=100, meetup_before=200, departure_after=110, departure_before=210, order_by='meetup_timestamp', order='desc')
        for call in self.db.execute.call_args_list:
            query = call.args[1]
            for fragment in ['is_private = 0', 'meetup_timestamp >= 100', 'meetup_timestamp <= 200', 'departure_timestamp >= 110', 'departure_timestamp <= 210', 'ORDER BY is_pinned DESC, meetup_timestamp desc, meetup_timestamp ASC']:
                self.assertIn(fragment, query)
        self.assertIn('LIMIT 1, 1', self.db.execute.call_args.args[1])
        self.assertEqual((result['total_items'], result['total_pages']), (3, 3))

    async def test_private_detail_hidden_from_anonymous_and_nonmember(self):
        for token, userid in [(None, -1), ('Bearer nonmember', None), ('Bearer guest', -1)]:
            self.ns['auth'].return_value = {'error': False, 'userid': userid, 'uid': 9, 'language': 'en'}
            self.db.fetchall.side_effect = [[self.row('[Image src="https://secret.example/a.png"]', 1)]]
            result = await self.ns['get_event'](self.request, self.response, 1, token)
            self.assertEqual(self.response.status_code, 404)
            self.assertEqual(result, {'error': 'event_not_found'})
        self.ns['GetUserInfo'].assert_not_awaited()

    async def test_member_private_detail_and_invalid_credentials(self):
        self.db.fetchall.side_effect = [[self.row('[Image src="/a.png"]', 1)]]
        result = await self.ns['get_event'](self.request, self.response, 1, 'Bearer member')
        self.assertEqual(result['banner_url'], 'https://hub.example/a.png')
        self.assertTrue(result['is_private'])
        for name, args in [('get_event', (1,)), ('get_list', ())]:
            self.ns['auth'].return_value = {'error': 'denied', 'code': 401}
            result = await self.ns[name](self.request, self.response, *args, authorization='Bearer invalid')
            self.assertEqual((self.response.status_code, result), (401, {'error': 'denied'}))

    async def test_truckersmp_import_banner_survives_read(self):
        imported = self.ns['_normalize_truckersmp_event']({'id': 123, 'name': 'TMP convoy', 'banner': 'https://static.truckersmp.com/banner.png', 'description': 'Imported text', 'start_at': '2026-12-01T12:00:00Z'})
        self.db.fetchall.side_effect = [[self.row(imported['description'])]]
        result = await self.ns['get_event'](self.request, self.response, 1)
        self.assertEqual(result['banner_url'], 'https://static.truckersmp.com/banner.png')


if __name__ == '__main__':
    unittest.main()
