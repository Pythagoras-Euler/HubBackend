import ast
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from fastapi import Header,Request,Response
SRC=Path(__file__).resolve().parents[1]/'src'
sys.path.insert(0,str(SRC))
from password_policy import valid_new_password


class PasswordEndpointTests(unittest.IsolatedAsyncioTestCase):
    def endpoint(self,path,name):
        ns=dict(Request=Request,Response=Response,Header=Header,time=time,valid_new_password=valid_new_password,
            ratelimit=AsyncMock(return_value=(False,{})),should_verify_captcha=lambda app:False,
            convertQuotation=lambda value:value,ml=SimpleNamespace(tr=lambda *a,**kw:a[1]),
            auth=AsyncMock(return_value={'error':False,'uid':6,'language':'en'}),isSecureAuth=AsyncMock(return_value=True))
        tree=ast.parse((SRC/path).read_text(encoding='utf-8'))
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name==name],type_ignores=[]),'endpoint','exec'),ns)
        return ns[name]

    def request(self,password,rows):
        self.db=SimpleNamespace(new_conn=AsyncMock(),execute=AsyncMock(),fetchall=AsyncMock(side_effect=rows),commit=AsyncMock())
        return SimpleNamespace(app=SimpleNamespace(db=self.db,config=SimpleNamespace(db_name='test',register_methods=['email'],language='en')),
            state=SimpleNamespace(dhrid='r'),json=AsyncMock(return_value={'email':'qa@example.test','password':password,'captcha-response':''}))

    def no_password_write(self):
        self.assertFalse(any('user_password' in call.args[1] for call in self.db.execute.await_args_list))
        self.db.commit.assert_not_awaited()

    async def test_registration_rejects_newline_bypass(self):
        response=Response()
        result=await self.endpoint('apis/auth/generic.py','post_register')(self.request('a\naaaaaa',[]),response)
        self.assertEqual(response.status_code,400);self.assertEqual(result['error'],'weak_password');self.no_password_write()

    async def test_settings_rejects_31_characters(self):
        response=Response()
        result=await self.endpoint('apis/user/password.py','patch_password')(self.request('Aa1!'+'x'*27,[[('',)],[('qa@example.test',)],[(1,)]]),response)
        self.assertEqual(response.status_code,400);self.assertEqual(result['error'],'weak_password');self.no_password_write()

    async def test_email_reset_rejects_utf8_overflow_without_consuming_token(self):
        response=Response()
        result=await self.endpoint('apis/auth/generic.py','post_email')(self.request('Aa1!'+'中'*23,[[(6,'reset-password/qa@example.test')],[],[]]),response,'rp-fixture')
        self.assertEqual(response.status_code,400);self.assertEqual(result['error'],'weak_password');self.no_password_write()
        self.assertFalse(any('DELETE FROM email_confirmation WHERE uid' in call.args[1] for call in self.db.execute.await_args_list))
