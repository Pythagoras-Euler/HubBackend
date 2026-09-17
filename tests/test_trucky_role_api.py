import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest
source=Path(__file__).resolve().parents[1]/'src/apis/tracker/trucky_roles.py'
nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.AsyncFunctionDef)]
class RoleApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns=dict(Header=lambda _:None,Request=object,Response=object,
            auth=AsyncMock(return_value={'error':False}),forbidden_roles=lambda app:{0,99},
            create_driver_role=AsyncMock(return_value=30),trainee_role=AsyncMock(return_value=10))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),self.ns)
        self.db=SimpleNamespace(new_conn=AsyncMock(),execute=AsyncMock(),fetchone=AsyncMock(return_value=('Dispatcher',)),commit=AsyncMock())
        self.request=SimpleNamespace(app=SimpleNamespace(db=self.db,config=SimpleNamespace(db_name='test'),roles={10:{},20:{},99:{}}),state=SimpleNamespace(dhrid='test'),json=AsyncMock())
        self.response=SimpleNamespace(status_code=200)
    async def test_denied_user_cannot_write_mapping(self):
        self.ns['auth'].return_value={'error':'Forbidden','code':403}
        await self.ns['put_mapping'](self.request,self.response,5,7,'test')
        self.assertEqual(self.response.status_code,403); self.db.execute.assert_not_awaited()
        self.assertEqual(self.ns['auth'].call_args.kwargs['required_permission'],['administrator'])
    async def test_admin_role_cannot_be_mapped(self):
        self.request.json.return_value={'action':'map','target_roleid':99}
        await self.ns['put_mapping'](self.request,self.response,5,7,'test')
        self.assertEqual(self.response.status_code,422); self.db.commit.assert_not_awaited()
    async def test_create_defaults_to_source_name_and_confirms(self):
        self.request.json.return_value={'action':'create'}
        await self.ns['put_mapping'](self.request,self.response,5,7,'test')
        self.ns['create_driver_role'].assert_awaited_once_with(self.request.app,'Dispatcher')
        self.assertEqual(self.db.execute.call_args.args[2],(30,5,7)); self.db.commit.assert_awaited_once()
    async def test_malformed_input_has_validation_response(self):
        self.request.json.return_value=[]
        await self.ns['put_mapping'](self.request,self.response,5,7,'test')
        self.assertEqual(self.response.status_code,422)
if __name__=='__main__': unittest.main()
