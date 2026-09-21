import ast,copy,json,time,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from active_deliveries import same_trip,expired,can_abandon,trip_from_delivery
from truckershub_routes import timestamp

def job():
 return {'steamid':'76561198000000001','game':'eut2','start_time':'2026-09-20T12:00:00Z','cargo':'Cars','source':{'city':'A','company':'X'},'destination':{'city':'B','company':'Y'}}

class MatchingTests(unittest.TestCase):
 def test_same_trip_with_different_source_ids_matches(self):
  a=job();b={**job(),'trackerid':999,'start_time':'2026-09-20T12:00:02Z'}
  self.assertTrue(same_trip(a,b))
 def test_missing_identity_and_different_trip_are_not_merged(self):
  for field,value in [('steamid',''),('steamid','76561198000000002'),('game','ats'),('cargo','Oil'),('start_time','2026-09-20T13:00:00Z')]:
   other={**job(),field:value};self.assertFalse(same_trip(job(),other))
 def test_seven_day_boundary_and_configurable_period(self):
  start=timestamp(job()['start_time'])
  self.assertFalse(expired(job(),start,start+7*86400-1,7))
  self.assertTrue(expired(job(),start,start+7*86400,7))
  self.assertFalse(expired(job(),start,start+7*86400,14))
 def test_unknown_start_expires_from_first_seen(self):
  self.assertFalse(expired({'start_time':None},1000,1100,7))
  self.assertTrue(expired({'start_time':None},1000,1000+7*86400,7))

class AbandonApiTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.user={'uid':7,'steamid':job()['steamid'],'roles':[],'error':False}
  self.staff=False
  path=Path(__file__).resolve().parents[1]/'src/apis/tracker/active.py'
  nodes=[n for n in ast.parse(path.read_text()).body if isinstance(n,ast.AsyncFunctionDef)]
  self.ns={'json':json,'time':time,'Request':object,'Response':object,'Header':lambda _:None,'auth':AsyncMock(return_value=self.user),'checkPerm':lambda *args:self.staff,'can_abandon':can_abandon}
  exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),self.ns)
  self.db=SimpleNamespace(new_conn=AsyncMock(),execute=AsyncMock(),fetchone=AsyncMock(return_value=('in_progress',json.dumps(job()))),commit=AsyncMock())
  self.lock=SimpleNamespace(acquire=lambda **kwargs:True,owned=lambda:True,release=Mock())
  self.req=SimpleNamespace(app=SimpleNamespace(db=self.db,redis=SimpleNamespace(lock=lambda *a,**k:self.lock),config=SimpleNamespace(db_name='qa',privacy=False)),state=SimpleNamespace(dhrid='qa'),json=AsyncMock(return_value={'confirmed':True}))
  self.response=SimpleNamespace(status_code=200)
 async def run_request(self):return await self.ns['abandon'](self.req,self.response,'trucky',42,'Bearer fixture')
 async def test_owner_can_abandon(self):
  result=await self.run_request();self.assertEqual(result,{'status':'abandoned'});self.db.commit.assert_awaited_once()
 async def test_other_driver_cannot_abandon(self):
  self.user['steamid']='76561198000000002';await self.run_request()
  self.assertEqual(self.response.status_code,403);self.db.commit.assert_not_awaited()
 async def test_authorized_staff_can_abandon(self):
  self.user['steamid']='76561198000000002';self.staff=True
  self.assertEqual(await self.run_request(),{'status':'abandoned'})
 async def test_confirmation_is_required(self):
  self.req.json.return_value={};await self.run_request()
  self.assertEqual(self.response.status_code,422);self.db.execute.assert_not_awaited()
 async def test_already_closed_is_not_rewritten(self):
  self.db.fetchone.return_value=('abandoned',json.dumps(job()));await self.run_request()
  self.assertEqual(self.response.status_code,409);self.db.commit.assert_not_awaited()

class LifecycleTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  path=Path(__file__).resolve().parents[1]/'src/functions/active_deliveries.py'
  nodes=[n for n in ast.parse(path.read_text()).body if isinstance(n,(ast.AsyncFunctionDef,ast.FunctionDef)) and n.name=='reconcile']
  self.ns={'json':json,'time':SimpleNamespace(time=lambda:timestamp(job()['start_time'])+86400),'timestamp':timestamp,'same_trip':same_trip,'expired':expired,'trip_from_delivery':lambda d:d,'decompress':lambda d:d,'snapshots':lambda app:{}}
  exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),self.ns)
  self.db=SimpleNamespace(execute=AsyncMock(),fetchall=AsyncMock(),commit=AsyncMock())
  self.lock=SimpleNamespace(acquire=lambda **kwargs:True,owned=lambda:True,release=Mock())
  self.app=SimpleNamespace(db=self.db,redis=SimpleNamespace(lock=lambda *a,**k:self.lock),config=SimpleNamespace(active_delivery_timeout_days=7))
 def row(self,sid=1,status='in_progress',payload=None):
  return ('trucky',sid,status,json.dumps(payload or job()),int(timestamp(job()['start_time'])))
 async def reconcile(self,rows,completed=()):
  self.db.fetchall.side_effect=[rows,list(completed)]
  await self.ns['reconcile'](self.app,'qa')
  return [c.args[2] for c in self.db.execute.await_args_list if c.args[1].startswith('UPDATE active_delivery')]
 async def test_completed_duplicate_closes_only_ghost(self):
  done=(100,json.dumps({'data':{'object':job()}}),3,2)
  self.assertEqual((await self.reconcile([self.row()], [done]))[0][0:3],('duplicate',int(self.ns['time'].time()),100))
 async def test_exact_id_wins_over_similar_completed_jobs(self):
  done=[(100,json.dumps({'data':{'object':job()}}),3,2),(101,json.dumps({'data':{'object':job()}}),3,1)]
  self.assertEqual((await self.reconcile([self.row()],done))[0][0:3],('completed',int(self.ns['time'].time()),101))
 async def test_ambiguous_completed_matches_are_not_merged(self):
  done=[(i,json.dumps({'data':{'object':job()}}),3,i) for i in (2,3)]
  self.assertEqual(await self.reconcile([self.row()],done),[])
 async def test_active_duplicates_leave_one_canonical(self):
  changes=await self.reconcile([self.row(1),self.row(2)])
  self.assertEqual(len(changes),1);self.assertEqual(changes[0][0],'duplicate');self.assertEqual(changes[0][4],2)
 async def test_expiration_and_closed_rows_are_not_reopened(self):
  self.ns['time']=SimpleNamespace(time=lambda:timestamp(job()['start_time'])+8*86400)
  changes=await self.reconcile([self.row(1),self.row(2,'abandoned')])
  self.assertEqual(len(changes),1);self.assertEqual(changes[0][0],'aborted')
 async def test_timeout_configuration_keeps_recent_job(self):
  self.app.config.active_delivery_timeout_days=14
  self.ns['time']=SimpleNamespace(time=lambda:timestamp(job()['start_time'])+8*86400)
  self.assertEqual(await self.reconcile([self.row()]),[])
 async def test_refresh_upsert_preserves_terminal_status(self):
  self.ns['snapshots']=lambda app:{('trucky',1):job()}
  await self.reconcile([self.row(status='abandoned')])
  insert=self.db.execute.await_args_list[0].args[1].split('ON DUPLICATE KEY UPDATE')[1]
  self.assertNotIn('status=',insert);self.db.commit.assert_awaited_once();self.lock.release.assert_called_once()
