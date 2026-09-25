import ast
import asyncio
import hashlib
import os
import sys
import time
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from PIL import Image

SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
from avatar_images import normalize_image


class AvatarStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse((SRC/'functions/avatars.py').read_text(encoding='utf-8'))
        self.fetch = AsyncMock()
        ns = dict(asyncio=asyncio,hashlib=hashlib,os=os,time=time,normalize_image=normalize_image,
                  fetch_image=self.fetch,_decode_slots=asyncio.Semaphore(2))
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)],type_ignores=[]),'avatar_store','exec'),ns)
        self.save, self.refresh = ns['save_avatar'], ns['refresh_avatar']
        self.provider = ns['provider_url'] = AsyncMock(return_value='https://cdn.example.com/image.png')
        self.db = SimpleNamespace(execute=AsyncMock(),fetchone=AsyncMock(side_effect=[('76561198000000001', None)]*2),commit=AsyncMock(),extend_conn=AsyncMock())
        self.app = SimpleNamespace(db=self.db,redis=Mock(),config=SimpleNamespace(domain='hub.example.com',prefix='/api'))
        self.app.redis.lock.return_value.acquire.return_value = True
        b=BytesIO();Image.new('RGB',(8,8),'red').save(b,format='PNG');self.png=b.getvalue()
        self.fetch.return_value=self.png

    async def test_upload_stores_local_png_without_changing_name(self):
        url = await self.save(self.app,'r',6,'upload',content=self.png)
        self.assertIn('/api/avatar/6/',url)
        self.fetch.assert_not_awaited()
        calls=self.db.execute.await_args_list
        insert=next(c.args for c in calls if c.args[1].startswith('INSERT'))
        self.assertEqual(insert[2][1],'upload')
        self.assertEqual(insert[2][4][:8],b'\x89PNG\r\n\x1a\n')
        self.assertFalse(any('SET name' in c.args[1] for c in calls))
        self.db.commit.assert_awaited_once()

    async def test_bad_image_never_commits(self):
        with self.assertRaises(ValueError):await self.save(self.app,'r',6,'upload',content=b'<svg/>')
        self.db.commit.assert_not_awaited()
        self.assertFalse(any('UPDATE user' in c.args[1] for c in self.db.execute.await_args_list))

    async def test_identity_change_during_fetch_rejects_image(self):
        self.db.fetchone.side_effect=[('76561198000000001',None),('76561198000000002',None)]
        with self.assertRaises(ValueError):await self.save(self.app,'r',6,'steam')
        self.db.commit.assert_not_awaited()

    async def test_provider_selection_is_persisted(self):
        await self.save(self.app,'r',6,'discord')
        self.provider.assert_awaited_once_with(self.app,'r','discord','76561198000000001',None)
        insert=next(c.args for c in self.db.execute.await_args_list if c.args[1].startswith('INSERT'))
        self.assertEqual(insert[2][1],'discord')

    async def test_changed_preference_not_overwritten_by_worker(self):
        self.db.fetchone.side_effect=[('76561198000000001',None)]*2+[('upload',)]
        with self.assertRaises(ValueError):await self.save(self.app,'r',6,'steam',expected_source='steam')
        self.db.commit.assert_not_awaited()

    async def test_refresh_failure_keeps_previous_avatar(self):
        self.db.fetchone.side_effect=[('steam',0),('76561198000000001',None)]
        self.provider.side_effect=ValueError('unavailable')
        await self.refresh(self.app,'r',6)
        self.db.commit.assert_not_awaited()

    async def test_uploaded_avatar_never_auto_refreshes(self):
        self.db.fetchone.side_effect=[('upload',0)]
        await self.refresh(self.app,'r',6)
        self.provider.assert_not_awaited()
