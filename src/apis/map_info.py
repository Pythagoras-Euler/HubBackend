"""Same-origin access to the five supported public map metadata files."""
import asyncio
import math
import time

from fastapi import Request, Response
from functions.arequests import arequests

MAPS = {("ets2", "base"), ("ets2", "promods"), ("ets2", "promods-classic"),
        ("ats", "base"), ("ats", "promods")}
_cache = {}
_locks = {key: asyncio.Lock() for key in MAPS}


def validate_metadata(data):
    keys = ("x1", "x2", "y1", "y2", "minZoom", "maxZoom")
    if not isinstance(data, dict) or any(type(data.get(k)) not in (int, float) or not math.isfinite(data[k]) for k in keys):
        raise ValueError("Invalid map metadata")
    if not (data["x1"] < data["x2"] and data["y1"] < data["y2"] and 0 <= data["minZoom"] <= data["maxZoom"] <= 30):
        raise ValueError("Invalid map bounds")
    return {key: data[key] for key in keys}


async def get_map_info(request: Request, response: Response, game: str, variant: str):
    key = (game, variant)
    if key not in MAPS:
        response.status_code = 404
        return {"error": "Map not found"}
    async with _locks[key]:
        entry = _cache.get(key)
        if entry is None or entry[0] <= time.monotonic():
            try:
                upstream = await arequests.get(None, f"https://map.charlws.com/{game}/{variant}/info/TileMapInfo.json", timeout=10)
                if upstream.status_code != 200:
                    raise ValueError("Map service unavailable")
                data = validate_metadata(upstream.json())
                _cache[key] = (time.monotonic() + 3600, data)
            except Exception:
                response.status_code = 502
                return {"error": "Map metadata temporarily unavailable"}
        else:
            data = entry[1]
    response.headers["Cache-Control"] = "public, max-age=3600"
    return data
