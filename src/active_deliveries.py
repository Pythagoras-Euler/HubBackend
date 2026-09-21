"""Conservative identity matching for local unfinished-transport lifecycle."""
from truckershub_routes import timestamp


def trip_from_delivery(data):
    return {'steamid': str((data.get('driver') or {}).get('steam_id') or ''),
            'game': (data.get('game') or {}).get('short_name'),
            'start_time': data.get('start_time'), 'cargo': (data.get('cargo') or {}).get('name'),
            'cargo_id': (data.get('cargo') or {}).get('unique_id'),
            **{side: {part: (data.get(side+'_'+part) or {}).get('name') for part in ('city','company')}
               for side in ('source','destination')}}


def same_trip(a, b):
    if not a.get('steamid') or a['steamid'] != b.get('steamid'):
        return False
    if not a.get('game') or a['game'] != b.get('game'):
        return False
    x, y = timestamp(a.get('start_time')), timestamp(b.get('start_time'))
    if x is None or y is None or abs(x-y) > 120:
        return False
    pairs = [(a.get('cargo'), b.get('cargo'))]
    pairs += [((a.get(s) or {}).get(p), (b.get(s) or {}).get(p))
              for s in ('source','destination') for p in ('city','company')]
    return all(isinstance(x,str) and isinstance(y,str) and x.strip() and
               x.strip().casefold() == y.strip().casefold() for x,y in pairs)


def can_abandon(user, job, staff=False):
    return bool(user and (staff or (user.get('steamid') and
                                   str(user['steamid']) == str(job.get('steamid')))))


def expired(job, first_seen, now, days):
    started = timestamp(job.get('start_time')) or first_seen
    return now - started >= days * 86400
