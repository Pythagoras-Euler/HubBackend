"""Validate a TMP account against the user's verified Steam identity."""
def verified_player(payload, steamid):
    if not isinstance(payload, dict) or payload.get('error') not in (False, 'false'):
        return None
    player = payload.get('response')
    if not isinstance(player, dict) or str(player.get('steamID64')) != str(steamid):
        return None
    value = player.get('id')
    if type(value) is not int or not 0 < value < 2**31:
        return None
    return player
