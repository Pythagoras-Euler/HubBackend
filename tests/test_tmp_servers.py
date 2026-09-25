import ast,unittest
from pathlib import Path
ns={}
p=Path(__file__).resolve().parents[1]/'src/apis/map_info.py'
nodes=[n for n in ast.parse(p.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='normalize_servers']
exec(compile(ast.Module(body=nodes,type_ignores=[]),str(p),'exec'),ns)
class ServerTests(unittest.TestCase):
 def test_new_servers_preserve_distinct_map_id_and_game(self):
  rows=[{'id':4,'mapid':2,'name':'Simulation 1','game':'ETS2','online':True,'promods':False,'displayorder':10},{'id':99,'mapid':75,'name':'New Server','game':'ATS','online':True,'promods':True,'displayorder':1}]
  result=ns['normalize_servers']({'error':'false','response':rows})
  self.assertEqual([s['id'] for s in result],[99,4]);self.assertEqual(result[1]['mapid'],2);self.assertTrue(result[0]['promods'])
 def test_no_map_id_does_not_fall_back_to_server_id(self):
  result=ns['normalize_servers']({'response':[{'id':99,'name':'Event','game':'ETS2'}]})
  self.assertIsNone(result[0]['mapid'])
 def test_rejected_payload_is_not_a_server_list(self):
  for value in ({'error':True,'response':[]},{'response':{}},'<html>'):
   with self.assertRaises(ValueError):ns['normalize_servers'](value)
