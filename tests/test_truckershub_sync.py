import ast,asyncio,json,time,sys,unittest
from urllib.parse import urlparse,parse_qs
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from truckershub_import import job_status,active_job
from test_truckershub_import import fixture
source=Path(__file__).resolve().parents[1]/'src/functions/truckershub_sync.py'
nodes=[n for n in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns=dict(urlparse=urlparse,parse_qs=parse_qs,asyncio=SimpleNamespace(sleep=AsyncMock()),json=json,time=time,datetime=datetime,timezone=timezone,Request=object,
                     get_key=AsyncMock(return_value='fixture-secret'),api_get=AsyncMock(),job_status=job_status,active_job=active_job,
                     months_since=lambda:[(2025,9)],compress=lambda x:x,decompress=lambda x:x,save_source=AsyncMock())
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),self.ns)
        self.ns['import_job']=AsyncMock(return_value=(1,2,1,800));self.ns['attach_route']=AsyncMock()
        lock=SimpleNamespace(acquire=lambda **kw:True,owned=lambda:True,release=Mock())
        self.redis=SimpleNamespace(lock=lambda *a,**kw:lock,hset=Mock(),set=Mock(),get=lambda k:None)
        self.last=''
        async def execute(rid,sql,args=None):self.last=sql
        async def one(rid):return (None,) if 'MAX(id)' in self.last else None
        async def all_rows(rid):return []
        self.db=SimpleNamespace(execute=AsyncMock(side_effect=execute),fetchone=AsyncMock(side_effect=one),fetchall=AsyncMock(side_effect=all_rows),commit=AsyncMock())
        self.req=SimpleNamespace(app=SimpleNamespace(redis=self.redis,db=self.db,config=SimpleNamespace()),state=SimpleNamespace(dhrid='test'))
    async def run_worker(self):
        with patch.dict(sys.modules,{'functions.userinfo':SimpleNamespace(checkPerm=lambda *a:True),'functions.dataop':SimpleNamespace(str2list=lambda s:[])}):
            await self.ns['reconcile'](self.req)
    def final(self):return self.redis.hset.call_args.kwargs['mapping']
    async def test_missing_token_is_idle(self):
        self.ns['get_key'].return_value=''
        await self.run_worker();self.ns['api_get'].assert_not_awaited()
    async def test_history_succeeds_without_paid_live_capability(self):
        self.ns['api_get'].side_effect=[[fixture()],fixture(),RuntimeError('TruckersHub HTTP 403')]
        await self.run_worker()
        self.assertEqual(self.final()['status'],'complete');self.assertEqual(self.final()['imported'],1)
        self.ns['import_job'].assert_awaited_once()
        self.assertTrue(any(c.kwargs['mapping'].get('live_status')=='unavailable' for c in self.redis.hset.call_args_list))
    async def test_invalid_job_is_retryable_without_stopping_next_job(self):
        broken=fixture();broken['jobID']=801
        self.ns['api_get'].side_effect=[[broken,fixture()],broken,fixture(),[]]
        self.ns['import_job'].side_effect=[ValueError('bad optional field'),(1,2,1,800)]
        await self.run_worker()
        self.assertEqual(self.final()['failed'],1);self.assertEqual(self.final()['imported'],1)
        self.assertEqual(self.final()['status'],'partial');self.ns['save_source'].assert_awaited_once()
    async def test_provider_failure_does_not_ack_pending_work(self):
        self.ns['api_get'].side_effect=RuntimeError('TruckersHub HTTP 429')
        await self.run_worker()
        self.assertEqual(self.final()['status'],'failed')
        self.assertEqual(self.final()['error'],'TruckersHub HTTP 429')
        self.assertFalse(any('UPDATE tracker_inbox' in c.args[1] for c in self.db.execute.call_args_list))
    async def test_active_job_never_enters_completed_import(self):
        raw=fixture();raw['realtime']['end']=None
        self.ns['api_get'].side_effect=[[raw],raw,[]]
        await self.run_worker();self.ns['import_job'].assert_not_awaited()
        snapshot=json.loads(next(c.args[1] for c in self.redis.set.call_args_list if c.args[0]=='truckershub-active'))
        self.assertEqual(snapshot['list'][0]['status'],'in_progress')




    async def test_all_pages_import_without_following_external_next_url(self):
        second=fixture();second['jobID']=801
        self.ns['api_get'].side_effect=[{'data':[fixture()],'links':{'next':'https://untrusted.invalid/jobs?page=2'}},fixture(),{'data':[second],'links':{'next':None}},second,[]]
        await self.run_worker()
        self.assertEqual(self.final()['imported'],2)
        self.assertEqual(self.ns['api_get'].call_args_list[2].args[3],'jobs?month=9&year=2025&page=2')
    async def test_repeated_page_is_rejected_without_acknowledging_receipts(self):
        self.ns['api_get'].side_effect=[{'data':[],'links':{'next':'?page=1'}}]
        await self.run_worker()
        self.assertEqual(self.final()['status'],'failed')

    async def test_route_requests_are_disabled_by_default(self):
        self.ns['api_get'].side_effect=[[],[]]
        await self.run_worker()
        self.assertEqual(self.final()['status'],'complete')
        self.ns['attach_route'].assert_not_awaited()
        self.assertFalse(any('route_retry_at<' in c.args[1] for c in self.db.execute.call_args_list))
    async def test_explicit_route_access_enables_route_fetch(self):
        self.req.app.config.truckershub_route_access=True
        self.db.fetchall.side_effect=lambda rid:[(800,1)] if 'route_retry_at<' in self.last else []
        self.ns['api_get'].side_effect=[[],[]]
        await self.run_worker()
        self.ns['attach_route'].assert_awaited_once()

if __name__=='__main__':unittest.main()
