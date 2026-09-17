import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest
source=Path(__file__).resolve().parents[1]/'src/apis/tracker/truckershub.py'
nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.AsyncFunctionDef)]
class SettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns=dict(Header=lambda _:None,Request=object,Response=object,authorize=AsyncMock(return_value=None),get_key=AsyncMock(return_value='private-test-token'),api_get=AsyncMock())
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),self.ns)
        self.db=SimpleNamespace(execute=AsyncMock(),commit=AsyncMock())
        self.request=SimpleNamespace(app=SimpleNamespace(db=self.db,redis=SimpleNamespace(hgetall=lambda _:{})),state=SimpleNamespace(dhrid='test'),json=AsyncMock())
        self.response=SimpleNamespace(status_code=200)
    async def test_settings_never_return_token(self):
        data=await self.ns['get_settings'](self.request,self.response,'test')
        self.assertEqual(data,{'configured':True,'sync':{}})
        self.assertNotIn('private-test-token',str(data))
    async def test_denied_user_cannot_write_or_contact_provider(self):
        self.ns['authorize'].return_value={'error':'Forbidden'}
        await self.ns['put_settings'](self.request,self.response,'test')
        self.db.execute.assert_not_awaited();self.ns['api_get'].assert_not_awaited()
    async def test_invalid_provider_token_does_not_replace_saved_token(self):
        self.request.json.return_value={'api_key':'invalid-test-token'}
        self.ns['api_get'].side_effect=RuntimeError('TruckersHub HTTP 401')
        result=await self.ns['put_settings'](self.request,self.response,'test')
        self.assertEqual(self.response.status_code,502);self.db.execute.assert_not_awaited()
        self.assertNotIn('invalid-test-token',str(result))
if __name__=='__main__':unittest.main()
