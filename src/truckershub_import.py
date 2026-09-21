"""TruckersHub's documented SI payload -> the Hub delivery contract.

Keep missing optional values as None. Never use provider revenue/THP as Hub
profit/points: income is game earnings; normalized events supply expenses.
"""
from datetime import datetime, timezone
import math
from truckershub_routes import timestamp


def number(value, default=None):
    if isinstance(value, bool):
        return default
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def obj(value):
    return value if isinstance(value, dict) else {}


def iso(value):
    stamp = timestamp(value)
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp is not None else None


def ident(value):
    value = obj(value)
    return {'unique_id': value.get('id'), 'name': value.get('name')}


def event_type(value):
    return ''.join(c for c in str(value).lower() if c.isalnum())


def job_status(job):
    status = event_type(job.get('jobStatus') or job.get('status', ''))
    types = [event_type(obj(e).get('type')) for e in job.get('events', []) if isinstance(e, dict)]
    if status in ('cancelled','canceled','jobcancelled','jobcanceled') or any(t in ('jobcancelled','jobcanceled') for t in types):
        return 'cancelled'
    if status in ('inprogress','started','active'):
        return 'in_progress'
    if status in ('completed', 'delivered', 'jobdelivered'):
        return 'completed'
    if iso(obj(job.get('realtime')).get('end')):
        return 'completed'
    return 'in_progress'


def vehicles(job):
    truck, trailer = obj(job.get('truck')), obj(job.get('trailer'))
    plate = obj(truck.get('licensePlate'))
    return {
        'truck': {'unique_id': obj(truck.get('model')).get('id') or truck.get('id'),
                  'name': obj(truck.get('model')).get('name') or truck.get('name'),
                  'brand': ident(truck), 'odometer': number(truck.get('odometer')),
                  'initial_odometer': number(truck.get('initialOdometer')),
                  'wheel_count': number(truck.get('wheels')), 'license_plate': plate.get('value'),
                  'license_plate_country': ident(plate.get('country')),
                  'current_damage': truck.get('current_damage'), 'total_damage': None,
                  'top_speed': number(job.get('topSpeed')), 'average_speed': number(job.get('avgSpeed'))},
        'trailers': [{'unique_id': trailer.get('id'), 'name': trailer.get('name'),
                      'body_type': trailer.get('bodyType'), 'chain_type': trailer.get('chainType'),
                      'wheel_count': number(trailer.get('wheels')), 'brand': ident(trailer.get('brand')),
                      'license_plate': obj(trailer.get('licensePlate')).get('value'),
                      'license_plate_country': ident(obj(trailer.get('licensePlate')).get('country')),
                      'current_damage': trailer.get('damage'), 'total_damage': None}] if trailer else [],
    }


def convert_job(job):
    if not isinstance(job, dict):
        raise ValueError('Invalid TruckersHub job')
    jid = job.get('jobID')
    if isinstance(jid, bool) or not str(jid).isdigit() or not 0 < int(jid) < 2**63:
        raise ValueError('Invalid TruckersHub job ID')
    driver, game, real = obj(job.get('driver')), obj(job.get('game')), obj(job.get('realtime'))
    steam = str(driver.get('steamID', ''))
    if len(steam) != 17 or not steam.isascii() or not steam.isdigit():
        raise ValueError('Missing Steam identity')
    game_id = {'ets2':'eut2','eut2':'eut2','ats':'ats'}.get(str(game.get('id','')).lower())
    if not game_id:
        raise ValueError('Unknown game')
    start, end = iso(real.get('start')), iso(real.get('end'))
    status = job_status(job)
    if not start or not end or status == 'in_progress' or timestamp(end) < timestamp(start):
        raise ValueError('Job has no valid terminal times')
    distance = number(job.get('distanceDriven'))
    income = number(job.get('income'))
    if distance is None or distance < 0 or (status == 'completed' and income is None):
        raise ValueError('Job is missing distance or income')
    cargo = obj(job.get('cargo'))
    events = [{'type':'job.started','real_time':start,'game_time':None,'location':None,'meta':{'autoLoaded':None}}]
    penalty = number(job.get('penalty'))
    for raw in job.get('events', []):
        raw = obj(raw)
        kind, detail = event_type(raw.get('type')), obj(raw.get('details') or raw.get('meta') or raw)
        meta, target = None, None
        amount = number(detail.get('amount'))
        if kind in ('fine','fined','playerfined') and amount is not None:
            target = 'fine'
            meta = {'amount': amount, 'offence': obj(detail.get('offence')).get('id') or detail.get('offence') or 'unknown',
                    'speed': number(detail.get('speed')), 'speed_limit': number(detail.get('speedLimit'))}
        elif kind in ('tollgate','tollgatepaid','ferry','train') and amount is not None:
            target = 'tollgate' if kind.startswith('toll') else kind
            meta = {'cost': amount, 'source': detail.get('source'), 'destination': detail.get('destination')}
        elif kind in ('refuelpaid', 'refuel') and amount is not None:
            target, meta = 'refuel', {'amount': amount}
        elif kind in ('collision', 'repair'):
            target, meta = kind, {k: number(detail.get(k)) for k in ('cabin','chassis','engine','transmission','wheels','total')}
        elif kind in ('jobcancelled','jobcanceled','cancelled','canceled'):
            penalty = number(detail.get('penalty'), penalty)
        if target:
            position = obj(raw.get('location'))
            location = {k.lower(): number(position.get(k)) for k in ('X','Y','Z')}
            if any(v is None for v in location.values()): location = None
            events.append({'type':target,'real_time':iso(raw.get('time')),'game_time':None,'location':location,'meta':meta})
    if status == 'cancelled' and penalty is None:
        penalty = 0  # No known penalty: do not invent a charge.
    terminal = 'job.delivered' if status == 'completed' else 'job.cancelled'
    events.append({'type':terminal,'real_time':end,'game_time':None,'location':None,
                   'meta':{'revenue':income,'distance':distance,'penalty':penalty,'autoParked':job.get('autoParked')}})
    data = {'id':int(jid), 'uuid':None,'object':'job','provider':'truckershub',
            'driver':{'steam_id':steam,'username':driver.get('username'),'profile_photo_url':driver.get('avatar')},
            'start_time':start,'stop_time':end,'time_spent':timestamp(end)-timestamp(start),
            'planned_distance':number(job.get('plannedDistance')),'driven_distance':distance,
            'adblue_used':None,'fuel_used':number(obj(job.get('fuel')).get('burned')),
            'is_special':job.get('isSpecial'),'is_late':job.get('isLate'),'market':obj(job.get('market')).get('id'),
            'cargo':{**ident(cargo),'mass':number(cargo.get('mass')),'damage':number(cargo.get('damage')),'details':None},
            'game':{'short_name':game_id,'language':cargo.get('language'),'had_police_enabled':obj(game.get('config')).get('isTrafficOffense'), 'realistic_settings':None},
            'multiplayer':{'type':obj(job.get('multiplayer')).get('type'),'meta':{'server':obj(job.get('multiplayer')).get('server')}} if job.get('multiplayer') else None,
            'events':events, **vehicles(job)}
    for side in ('source','destination'):
        for part in ('city','company'):
            data[side+'_'+part] = ident(obj(job.get(side)).get(part))
    return {'type':terminal,'data':{'object':data}}


def active_job(job):
    if not isinstance(job, dict) or job_status(job) != 'in_progress':
        return None
    driver = obj(job.get('driver'))
    real = obj(job.get('realtime'))
    return {'tracker':'truckershub','trackerid':str(job.get('jobID') or driver.get('steamID') or ''),
            'status':'in_progress','driver':driver.get('username'), 'steamid':str(driver.get('steamID') or ''),
            'source':{k:obj(obj(job.get('source')).get(k)).get('name') for k in ('city','company')},
            'destination':{k:obj(obj(job.get('destination')).get(k)).get('name') for k in ('city','company')},
            'cargo':obj(job.get('cargo')).get('name'),'distance':number(job.get('distanceDriven')),
            'start_time':iso(real.get('start')),'stop_time':None,**vehicles(job)}


def months_since(year=2025, month=9, now=None):
    now = now or datetime.now(timezone.utc)
    result=[]
    while (year,month) <= (now.year,now.month):
        result.append((year,month))
        year, month = (year+1,1) if month == 12 else (year,month+1)
    return list(reversed(result))


def merge_active(items):
    """Coalesce only strongly identified simultaneous cross-provider live records."""
    merged=[]
    for item in items:
        match=None
        for previous in merged:
            a,b=timestamp(item.get('start_time')),timestamp(previous.get('start_time'))
            if not item.get('steamid') or item['steamid']!=previous.get('steamid') or a is None or b is None or abs(a-b)>120:
                continue
            if item.get('tracker')==previous.get('tracker'):
                continue
            if not item.get('cargo') or item['cargo']!=previous.get('cargo'):
                continue
            if any(not (item.get(k) or {}).get('city') or item[k]!=previous.get(k) for k in ('source','destination')):
                continue
            match=previous;break
        if match is None:
            merged.append({**item,'trackers':[item.get('tracker','trucky')]})
        else:
            match['trackers']=list(dict.fromkeys(match['trackers']+[item['tracker']]))
            match['stale']=match.get('stale',False) and item.get('stale',False)
    return merged
