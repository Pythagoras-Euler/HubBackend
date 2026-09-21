import ast, hmac, time, sys, hashlib, json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from truckershub_routes import webhook_token, api_payload
source=Path(__file__).resolve().parents[1]/'src/apis/tracker/truckershub.py'
nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.AsyncFunctionDef)]
class SettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns=dict(hashlib=hashlib,json=json,compress=lambda x:x,decompress=lambda x:x,hmac=hmac,time=time,webhook_token=webhook_token,Header=lambda _:None,Request=object,Response=object,authorize=AsyncMock(return_value=None),get_key=AsyncMock(return_value='private-test-token'),api_get=AsyncMock())
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),self.ns)
        self.db=SimpleNamespace(execute=AsyncMock(),commit=AsyncMock(),new_conn=AsyncMock(),fetchall=AsyncMock(return_value=[]))
        self.request=SimpleNamespace(app=SimpleNamespace(config=SimpleNamespace(db_name='test'),db=self.db,redis=SimpleNamespace(hgetall=lambda _:{})),state=SimpleNamespace(dhrid='test'),json=AsyncMock())
        self.response=SimpleNamespace(status_code=200)
        async def stream():
            yield b'{"type":"test"}'
        self.request.stream=stream
    async def test_settings_never_return_token(self):
        data=await self.ns['get_settings'](self.request,self.response,'test')
        self.assertTrue(data['configured']); self.assertEqual(data['sync'],{})
        self.assertTrue(data['webhook_path'].startswith('/truckershub/update?token='))
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
    async def test_webhook_rejects_missing_or_wrong_token(self):
        for token in ('', 'wrong'):
            await self.ns['post_update'](self.request, self.response, token)
            self.assertEqual(self.response.status_code, 403)
    async def test_webhook_acknowledges_and_coalesces_retries(self):
        redis = SimpleNamespace(set=Mock(side_effect=[True, False]),delete=Mock(),hset=Mock())
        self.request.app.redis = redis
        for _ in range(2):
            result = await self.ns['post_update'](self.request,self.response,webhook_token('private-test-token'))
            self.assertEqual(self.response.status_code, 200)
            self.assertEqual(result, {'received': True})
        self.assertEqual(self.db.new_conn.await_count, 2)
        self.assertEqual(self.db.commit.await_count,2)
        self.request.json.assert_not_awaited()
        self.assertTrue(all('INSERT IGNORE INTO tracker_inbox' in call.args[1] for call in self.db.execute.call_args_list))
    def test_api_envelopes_and_errors(self):
        for data in ({'id': 7}, [{'jobID': 9}], [{'position': {'X': 1,'Z': 2}}]):
            self.assertEqual(api_payload(data),data)
            self.assertEqual(api_payload({'success':True,'data':data}),data)
        with self.assertRaises(ValueError):
            api_payload({'success':False,'data':{'id':7}})
        with self.assertRaises(ValueError):
            api_payload({'error':True,'message':'private provider text'})
if __name__=='__main__':unittest.main()
