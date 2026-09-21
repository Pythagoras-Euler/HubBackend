import ast,hashlib,json,time,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from truckershub_routes import timestamp
from truckershub_import import convert_job
from test_truckershub_import import fixture
source=Path(__file__).resolve().parents[1]/'src/functions/tracker_sources.py'
nodes=[n for n in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]

class SourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns=dict(hashlib=hashlib,json=json,time=time,timestamp=timestamp,compress=lambda x:x,decompress=lambda x:x)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),self.ns)
        self.db=SimpleNamespace(execute=AsyncMock(),fetchone=AsyncMock(side_effect=[(1,),(1,),None,None,None,None,None]),fetchall=AsyncMock(return_value=[]),commit=AsyncMock(),extend_conn=AsyncMock())
        self.req=SimpleNamespace(app=SimpleNamespace(config=SimpleNamespace(db_name='fixture'),db=self.db),state=SimpleNamespace(dhrid='test'))
        self.core=AsyncMock(return_value=(1,2,1,800));self.raw=fixture();self.converted=convert_job(self.raw)
    async def test_unique_source_enters_shared_core(self):
        result=await self.ns['ingest'](self.req,self.raw,self.converted,'truckershub',self.core,historical=True)
        self.assertEqual(len(result),4);self.core.assert_awaited_once()
        self.assertEqual(sum('RELEASE_LOCK' in c.args[1] for c in self.db.execute.call_args_list),2)
    async def test_known_source_never_enters_core(self):
        self.db.fetchone.side_effect=[(1,),(1,),(9,'linked'),None,None]
        result=await self.ns['ingest'](self.req,self.raw,self.converted,'truckershub',self.core)
        self.assertEqual(result[0],409);self.core.assert_not_awaited()
    async def test_strong_cross_source_match_links_without_counting_twice(self):
        self.db.fetchall.return_value=[(17,json.dumps(self.converted),1)]
        result=await self.ns['ingest'](self.req,self.raw,self.converted,'truckershub',self.core)
        self.assertEqual(result[0],409);self.core.assert_not_awaited()
        self.assertTrue(any('INSERT INTO delivery_source' in c.args[1] and c.args[2][2]==17 for c in self.db.execute.call_args_list))
    async def test_ambiguous_match_requires_review(self):
        self.db.fetchall.return_value=[(17,json.dumps(self.converted),1),(18,json.dumps(self.converted),1)]
        result=await self.ns['ingest'](self.req,self.raw,self.converted,'truckershub',self.core)
        self.assertEqual(result[0],422);self.core.assert_not_awaited()
    async def test_failure_rolls_back_and_releases_both_locks(self):
        self.core.side_effect=ValueError('fixture failure')
        with self.assertRaises(ValueError):await self.ns['ingest'](self.req,self.raw,self.converted,'truckershub',self.core)
        self.assertTrue(any(c.args[1]=='ROLLBACK' for c in self.db.execute.call_args_list))
        self.assertEqual(sum('RELEASE_LOCK' in c.args[1] for c in self.db.execute.call_args_list),2)
    def test_other_driver_is_not_a_duplicate(self):
        a=self.converted['data']['object'];b=convert_job(fixture())['data']['object'];b['driver']['steam_id']='76561198000000002'
        self.assertFalse(self.ns['same_trip'](a,b))

if __name__=='__main__':unittest.main()
