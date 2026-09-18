"""Match recorded TruckersHub routes to existing deliveries without creating jobs."""
from datetime import datetime, timezone
import math


def timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def same_delivery(local, remote):
    """Conservative cross-provider match: identity, game, depots, cargo, both times."""
    if str(local.get('driver', {}).get('steam_id') or '') != str(remote.get('driver', {}).get('steamID') or ''):
        return False
    if not local.get('driver', {}).get('steam_id'):
        return False
    games = {'eut2': 'ets2', 'ets2': 'ets2', 'ats': 'ats'}
    local_game = games.get(str(local.get('game', {}).get('short_name', '')).lower())
    if local_game is None or local_game != games.get(str(remote.get('game', {}).get('id', '')).lower()):
        return False
    for local_key, remote_key, part in [('source_city', 'source', 'city'), ('source_company', 'source', 'company'), ('destination_city', 'destination', 'city'), ('destination_company', 'destination', 'company')]:
        left = str((local.get(local_key) or {}).get('unique_id') or '').strip().casefold()
        right = str(((remote.get(remote_key) or {}).get(part) or {}).get('id') or '').strip().casefold()
        if not left or left != right:
            return False
    cargo = str((local.get('cargo') or {}).get('unique_id') or '').strip().casefold()
    if not cargo or cargo != str((remote.get('cargo') or {}).get('id') or '').strip().casefold():
        return False
    for lk, rk in [('start_time', 'start'), ('stop_time', 'end')]:
        a, b = timestamp(local.get(lk)), timestamp((remote.get('realtime') or {}).get(rk))
        if a is None or b is None or abs(a-b) > 120:
            return False
    return True


def route_points(route):
    if not isinstance(route, list):
        raise ValueError('Unexpected route response')
    result = []
    for sample in route:
        position = sample.get('position') if isinstance(sample, dict) else None
        if not isinstance(position, dict):
            continue
        x, z = position.get('X'), position.get('Z')
        if isinstance(x, bool) or isinstance(z, bool) or x is None or z is None:
            continue
        try:
            x, z = float(x), float(z)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(x) or not math.isfinite(z) or max(abs(x), abs(z)) > 1e7 or (x == 0 and z == 0):
            continue
        point = (round(x, 2), round(z, 2))
        if not result or point != result[-1]:
            result.append(point)
    return result


def encode_route(game, points):
    if len(points) < 2:
        raise ValueError('Route needs at least two valid positions')
    game_id = 1 if game in ('eut2', 'ets2') else 2
    # Version 1 stores absolute x,y,z and is supported by the existing detail API.
    return f'{game_id},,v1;' + ';'.join(f'{x},0,{z}' for x, z in points)


def api_payload(payload):
    """Accept raw API objects and the provider's JSON response envelope."""
    if isinstance(payload, dict):
        if payload.get('error') or payload.get('success') is False:
            raise ValueError('TruckersHub API rejected the request')
        if 'data' in payload and isinstance(payload['data'], (dict, list)):
            return payload['data']
    return payload


def webhook_token(key):
    import hmac
    return hmac.new(key.encode(), b'drivershub:truckershub:route-webhook:v1', 'sha256').hexdigest()
