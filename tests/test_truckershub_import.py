import copy
import sys
import unittest
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from truckershub_import import convert_job,active_job,months_since,job_status,merge_active


def fixture():
    return {'jobID':800,'driver':{'steamID':'76561198000000001','username':'Fixture driver'},
            'game':{'id':'ets2'},'realtime':{'start':'2025-09-01T10:00:00Z','end':'2025-09-01T11:00:00Z'},
            'income':1200,'distanceDriven':120,'plannedDistance':100,'topSpeed':25,'avgSpeed':20,
            'fuel':{'burned':40},'cargo':{'id':'cars','name':'Cars','mass':12000,'damage':0.02},
            'source':{'city':{'id':'a','name':'A'},'company':{'id':'x','name':'X'}},
            'destination':{'city':{'id':'b','name':'B'},'company':{'id':'y','name':'Y'}},
            'truck':{'id':'volvo','name':'Volvo','model':{'id':'volvo.fh','name':'FH'}},
            'trailer':{'name':'Trailer','bodyType':'flatbed'},'events':[]}


class ConversionTests(unittest.TestCase):
    def test_si_units_times_vehicles_and_profit_basis(self):
        raw=fixture();raw['revenue']=999999;raw['THP']=99999
        before=copy.deepcopy(raw);value=convert_job(raw)['data']['object']
        self.assertEqual(raw,before)
        self.assertEqual(value['truck']['top_speed'],25)
        self.assertEqual(value['fuel_used'],40)
        self.assertEqual(value['time_spent'],3600)
        self.assertEqual(value['events'][-1]['meta']['revenue'],1200)
        self.assertEqual(value['trailers'][0]['body_type'],'flatbed')
    def test_missing_optional_fields_stay_unknown(self):
        raw=fixture();raw.pop('topSpeed');raw.pop('fuel');raw['cargo'].pop('mass');raw.pop('trailer')
        value=convert_job(raw)['data']['object']
        self.assertIsNone(value['truck']['top_speed']);self.assertIsNone(value['fuel_used'])
        self.assertIsNone(value['cargo']['mass']);self.assertEqual(value['trailers'],[])
    def test_cancelled_penalty_and_fine(self):
        raw=fixture();raw['events']=[{'type':'fine','details':{'amount':200,'offence':'speeding_camera'}},{'type':'job.cancelled','details':{'penalty':350}}]
        value=convert_job(raw)
        self.assertEqual(value['type'],'job.cancelled')
        self.assertEqual(value['data']['object']['events'][-1]['meta']['penalty'],350)
        self.assertIsNone(value['data']['object']['events'][1]['meta']['speed'])
    def test_rejects_missing_identity_and_invalid_terminal_metrics(self):
        for key,value in [('jobID',True),('income',float('nan')),('distanceDriven',-1),('driver',{})]:
            raw=fixture();raw[key]=value
            with self.assertRaises(ValueError):convert_job(raw)
    def test_active_never_becomes_completed_delivery(self):
        raw=fixture();raw['realtime']['end']=None
        self.assertEqual(job_status(raw),'in_progress')
        self.assertEqual(active_job(raw)['tracker'],'truckershub')
        with self.assertRaises(ValueError):convert_job(raw)
    def test_history_begins_in_september_2025(self):
        self.assertEqual(months_since(now=datetime(2025,10,2,tzinfo=timezone.utc)),[(2025,10),(2025,9)])

if __name__=='__main__':unittest.main()

class ActiveMergeTests(unittest.TestCase):
    def test_cross_provider_active_trip_is_one_card_without_mutating_sources(self):
        raw=fixture();raw['realtime']['end']=None
        th=active_job(raw);trucky=copy.deepcopy(th);trucky.update(tracker='trucky',trackerid='900',stale=True)
        result=merge_active([trucky,th])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['trackers'],['trucky','truckershub'])
        self.assertFalse(result[0]['stale'])
        self.assertNotIn('trackers',trucky)
    def test_different_driver_or_missing_identity_is_not_coalesced(self):
        raw=fixture();raw['realtime']['end']=None
        th=active_job(raw);other=copy.deepcopy(th);other.update(tracker='trucky',steamid='')
        self.assertEqual(len(merge_active([th,other])),2)

class RealPayloadTests(unittest.TestCase):
    def test_millisecond_terminal_times_and_job_status(self):
        raw=fixture();raw['jobStatus']='Completed'
        raw['realtime']={'start':1756720800000,'end':1756724400123,'timeTaken':3600123}
        value=convert_job(raw)['data']['object']
        self.assertEqual(job_status(raw),'completed')
        self.assertEqual(value['start_time'],'2025-09-01T10:00:00+00:00')
        self.assertAlmostEqual(value['time_spent'],3600.123,places=3)
        self.assertIsNone(active_job(raw))
    def test_seconds_and_numeric_strings(self):
        raw=fixture();raw['realtime']={'start':'1756720800000','end':1756724400}
        self.assertEqual(convert_job(raw)['data']['object']['time_spent'],3600)
    def test_invalid_numeric_times_are_not_completed_jobs(self):
        for value in (True,0,-1,float('nan'),float('inf'),10**30):
            raw=fixture();raw['realtime']['end']=value
            with self.assertRaises(ValueError):convert_job(raw)
    def test_completed_without_end_fails_instead_of_showing_active(self):
        raw=fixture();raw['jobStatus']='Completed';raw['realtime']['end']=None
        self.assertIsNone(active_job(raw))
        with self.assertRaises(ValueError):convert_job(raw)
    def test_flat_paid_events_are_retained(self):
        raw=fixture();raw['events']=[{'type':'refuel-paid','amount':100,'time':1756720900000}, {'type':'fine','amount':200,'offence':'speeding_camera','time':1756721000000}]
        events=convert_job(raw)['data']['object']['events']
        self.assertEqual(events[1]['type'],'refuel')
        self.assertEqual(events[2]['meta']['amount'],200)
        self.assertIsNone(events[2]['meta']['speed'])
        self.assertTrue(events[1]['real_time'].startswith('2025-09-01'))
