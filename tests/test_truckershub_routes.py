import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from truckershub_routes import same_delivery,route_points,encode_route
class RouteTests(unittest.TestCase):
    def setUp(self):
        self.local={'driver':{'steam_id':'76561198000000001'},'game':{'short_name':'eut2'},'cargo':{'unique_id':'cars'},'start_time':'2026-09-17T10:00:00Z','stop_time':'2026-09-17T11:00:00Z'}
        self.remote={'driver':{'steamID':'76561198000000001'},'game':{'id':'ets2'},'cargo':{'id':'cars'},'realtime':{'start':'2026-09-17T10:01:00Z','end':'2026-09-17T11:00:00Z'},'source':{},'destination':{}}
        for location in ('source','destination'):
            for part in ('city','company'):
                key=location+'_'+part
                self.local[key]={'unique_id':key}; self.remote[location][part]={'id':key}
    def test_same_trip_matches_across_providers(self):
        self.assertTrue(same_delivery(self.local,self.remote))
    def test_identity_game_cargo_depot_and_time_must_agree(self):
        for path,value in [(('driver','steamID'),'76561198000000002'),(('game','id'),'ats'),(('cargo','id'),'logs'),(('realtime','start'),'2026-09-17T10:03:00Z'),(('realtime','end'),None)]:
            other=copy.deepcopy(self.remote); other[path[0]][path[1]]=value
            self.assertFalse(same_delivery(self.local,other))
        other=copy.deepcopy(self.remote); other['source']['company']['id']='other'
        self.assertFalse(same_delivery(self.local,other))
    def test_missing_identifiers_do_not_match(self):
        self.assertFalse(same_delivery({},{}))
        self.local['game']={}; self.remote['game']={}
        self.assertFalse(same_delivery(self.local,self.remote))
    def test_route_uses_recorded_coordinates_and_removes_invalid_points(self):
        route=[{'position':{'X':100,'Z':-200}},{'position':{'X':100,'Z':-200}}, {'position':{'X':None,'Z':2}}, {'position':{'X':float('nan'),'Z':2}}, {'position':{'X':0,'Z':0}}, {'position':{'X':110,'Z':-220}}]
        points=route_points(route)
        self.assertEqual(points,[(100,-200),(110,-220)])
        self.assertEqual(encode_route('eut2',points),'1,,v1;100.0,0,-200.0;110.0,0,-220.0')
    def test_short_routes_are_not_stored(self):
        with self.assertRaises(ValueError):encode_route('ats',[(1,2)])
if __name__=='__main__':unittest.main()
