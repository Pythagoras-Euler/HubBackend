import ast
import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from fastapi import Header, Request, Response

SRC=Path(__file__).resolve().parents[1]/'src'
sys.path.insert(0,str(SRC))
from avatar_images import MAX_BYTES


class AvatarApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.auth=AsyncMock(return_value={'error':False,'uid':6})
        self.save=AsyncMock(return_value='https://hub.example/api/avatar/6/test.png')
        ns=dict(Request=Request,Response=Response,Header=Header,MAX_BYTES=MAX_BYTES,json=json,re=re,
                auth=self.auth,ratelimit=AsyncMock(return_value=(False,{})),save_avatar=self.save)
        tree=ast.parse((SRC/'apis/user/avatar.py').read_text(encoding='utf-8'))
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)],type_ignores=[]),'avatar_api','exec'),ns)
        self.put=ns['put_avatar']
        self.app=SimpleNamespace(db=SimpleNamespace(new_conn=AsyncMock()),config=SimpleNamespace(db_name='test'))

    def request(self,body):
        return Request({'type':'http','app':self.app,'state':{'dhrid':'r'}},receive=AsyncMock(return_value={'type':'http.request','body':body,'more_body':False}))

    async def test_anonymous_rejected_before_image_processing(self):
        self.auth.return_value={'error':'Unauthorized','code':401}
        response=Response()
        await self.put(self.request(b'bad'),response,'upload')
        self.assertEqual(response.status_code,401)
        self.save.assert_not_awaited()

    async def test_oversized_upload_rejected(self):
        response=Response()
        await self.put(self.request(b'x'*(MAX_BYTES+1)),response,'upload')
        self.assertEqual(response.status_code,413)
        self.save.assert_not_awaited()

    async def test_unsupported_source_rejected(self):
        response=Response()
        await self.put(self.request(b'{}'),response,'file')
        self.assertEqual(response.status_code,422)
        self.save.assert_not_awaited()

    async def test_bad_external_json_rejected(self):
        response=Response()
        await self.put(self.request(b'[]'),response,'external')
        self.assertEqual(response.status_code,422)
        self.save.assert_not_awaited()

    async def test_cannot_select_another_user(self):
        result=await self.put(self.request(b'{"url":"https://cdn.example/a.png","uid":999}'),Response(),'external')
        self.assertEqual(result['source'],'external')
        self.assertEqual(self.save.await_args.args[2],6)
