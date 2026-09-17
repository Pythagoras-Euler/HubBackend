import ast, asyncio, json, time
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import sys,unittest
from unittest.mock import AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from truckershub_routes import same_delivery,route_points,encode_route,timestamp
source=Path(__file__).resolve().parents[1]/'src/functions/truckershub_routes.py'
nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.AsyncFunctionDef)]
class RouteSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns=dict(asyncio=SimpleNamespace(sleep=AsyncMock()),datetime=datetime,timezone=timezone,json=json,time=time,timestamp=timestamp,same_delivery=same_delivery,route_points=route_points,encode_route=encode_route,compress=lambda x:x,decompress=lambda x:x)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),self.ns)
        self.ns['get_key']=AsyncMock(return_value='test-only-token')
        self.ns['api_get']=AsyncMock()
        lock=SimpleNamespace(acquire=lambda **k:True,owned=lambda:True,release=lambda:None)
        self.app=SimpleNamespace(config=SimpleNamespace(plugins=['route']),redis=SimpleNamespace(lock=lambda *a,**k:lock,get=lambda _:None,set=lambda *a,**k:None,hset=lambda *a,**k:None),db=SimpleNamespace(execute=AsyncMock(),fetchall=AsyncMock(return_value=[]),fetchone=AsyncMock(return_value=None),commit=AsyncMock()))
        self.request=SimpleNamespace(app=self.app,state=SimpleNamespace(dhrid='test'))
        self.obj={'driver':{'steam_id':'76561198000000001'},'game':{'short_name':'eut2'},'cargo':{'unique_id':'cars'},'start_time':'2026-09-17T10:00:00Z','stop_time':'2026-09-17T11:00:00Z'}
        self.job={'jobID':44,'driver':{'steamID':'76561198000000001'},'game':{'id':'ets2'},'cargo':{'id':'cars'},'realtime':{'start':self.obj['start_time'],'end':self.obj['stop_time']},'source':{},'destination':{}}
        for loc in ('source','destination'):
            for part in ('city','company'):
                self.obj[loc+'_'+part]={'unique_id':loc+part}; self.job[loc][part]={'id':loc+part}
    async def test_missing_key_makes_no_external_requests(self):
        self.ns['get_key'].return_value=''
        await self.ns['sync_routes'](self.request)
        self.ns['api_get'].assert_not_awaited();self.app.db.execute.assert_not_awaited()
    async def test_unique_match_writes_only_telemetry(self):
        self.app.db.fetchall.return_value=[(1,6,json.dumps({'data':{'object':self.obj}}))]
        self.ns['api_get'].side_effect=[[self.job],[{'position':{'X':1,'Z':2}},{'position':{'X':3,'Z':4}}]]
        await self.ns['sync_routes'](self.request)
        writes=[c.args[1] for c in self.app.db.execute.call_args_list if c.args[1].startswith(('UPDATE','INSERT','DELETE'))]
        self.assertEqual(len(writes),1);self.assertTrue(writes[0].startswith('INSERT INTO telemetry'))
    async def test_ambiguous_provider_jobs_never_link(self):
        self.app.db.fetchall.return_value=[(1,6,json.dumps({'data':{'object':self.obj}}))]
        self.ns['api_get'].return_value=[self.job,{**self.job,'jobID':45}]
        await self.ns['sync_routes'](self.request)
        self.assertFalse(any(c.args[1].startswith('INSERT') for c in self.app.db.execute.call_args_list))
    async def test_provider_error_does_not_modify_jobs(self):
        self.app.db.fetchall.return_value=[(1,6,json.dumps({'data':{'object':self.obj}}))]
        self.ns['api_get'].side_effect=RuntimeError('TruckersHub HTTP 503')
        await self.ns['sync_routes'](self.request)
        self.assertFalse(any(c.args[1].startswith(('INSERT','UPDATE','DELETE')) for c in self.app.db.execute.call_args_list))
if __name__=='__main__':unittest.main()
