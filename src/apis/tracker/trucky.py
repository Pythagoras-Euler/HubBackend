# Copyright (C) 2022-2026 CharlesWithC All rights reserved.
# Author: @CharlesWithC

import copy
import hashlib
import hmac
import json
import time
from typing import Optional
from urllib.parse import parse_qs

from fastapi import Header, Request, Response

import multilang as ml
from functions import *
from trucky_time import normalize_time, time_seconds


def convert_format(data):
    job_event_type_mapping = {"job_completed": "job.delivered", "job_canceled": "job.cancelled"}
    if data["event"] not in job_event_type_mapping.keys():
        return None
    job_event_type = job_event_type_mapping[data["event"]]

    d = copy.deepcopy(data["data"])
    # first convert data
    convert_distance = ["driven_distance_km", "planned_distance_km", "max_speed_kmh", "vehicle_odometer_end_km", "vehicle_odometer_start_km"]
    if d["distance_unit"] == "mi":
        for key in convert_distance:
            if d[key] is None:
                if d["_".join(key.split("_")[:-1])] is None:
                    d[key] = 0
                else:
                    d[key] = d["_".join(key.split("_")[:-1])] * 1.609344
    elif d["distance_unit"] == "km":
        for key in convert_distance:
            if d[key] is None:
                d[key] = d["_".join(key.split("_")[:-1])]
    if d["real_driven_distance_km"] is None:
        d["real_driven_distance_km"] = d["driven_distance_km"]
    try:
        d["average_speed_kmh"] = round((d["real_driven_distance_km"] / (d["real_driving_time_seconds"] / 3600)) / d["max_map_scale"])
    except: # in case of division by zero
        d["average_speed_kmh"] = 0
    if d["volume_unit"] == "gal":
        if d["fuel_used_l"] is None:
            if d["fuel_used"] is None:
                d["fuel_used_l"] = 0
            else:
                d["fuel_used_l"] = d["fuel_used"] * 3.785412
    elif d["volume_unit"] == "l":
        if d["fuel_used_l"] is None:
            d["fuel_used_l"] = d["fuel_used"]
    multiplayer = None
    if d["game_mode"] != "sp":
        multiplayer = {"type": d["game_mode"], "meta": {"server": d["server"]}}
    cargo_details = d["cargo_definition"]
    if cargo_details is not None:
        del cargo_details["id"], cargo_details["name"], cargo_details["in_game_id"], cargo_details["game_id"], cargo_details["localized_names"]
    events = []
    event_type_mapping = {"teleport": "teleport", "truck_fixed": "repair", "job_resumed": "job.resumed", "fined": "fine", "tollgate_paid": "tollgate", "collision": "collision", "transport_used": "transport", "refuel": "refuel"}
    # transport needs to be handled manually
    events.append({"location": None, "real_time": normalize_time(d["started_at"]), "time": time_seconds(d["started_at"]), "type": "job.started", "meta": {"autoLoaded": d["auto_load"]}})
    for event in d["events"]:
        et = event_type_mapping[event["event_type"]]
        meta = {}
        if et == "fine":
            meta = event["attributes"]
        elif et == "tollgate":
            meta = {"cost": event["attributes"]["amount"]}
        elif et == "collision":
            meta = {"cargo_damage": round(event["attributes"]["damage"]["cargoDamage"] / 100, 2), "chassis_damage": round(event["attributes"]["damage"]["chassisDamage"] / 100, 2), "trailers_damage": round(event["attributes"]["damage"]["trailersDamage"] / 100, 2), "total_damage_difference": round(event["attributes"]["damage"]["totalDamageDifference"] / 100, 2)}
        elif et == "transport":
            et = event["attributes"]["transport_type"]
            meta = {"cost": event["attributes"]["amount"], "source_id": event["attributes"]["source_id"], "source_name": event["attributes"]["source"], "target_id": event["attributes"]["target_id"], "target_name": event["attributes"]["target"]}
        events.append({"location": {"x": event["x"], "y": event["y"], "z": event["z"]}, "real_time": normalize_time(event["created_at"]), "time": time_seconds(event["created_at"]), "type": et, "meta": meta})
    if job_event_type == "job.delivered":
        events.append({"location": None, "real_time": normalize_time(d["completed_at"]), "time": time_seconds(d["completed_at"]), "type": "job.delivered", "meta": {"revenue": d["income"], "revenue_details": d["income_details"], "earnedXP": None, "cargoDamage": round(d["cargo_damage"] / 100, 2), "distance": d["real_driven_distance_km"], "timeTaken": d["real_driving_time_seconds"], "autoParked": d["auto_park"]}}) # revenue_details is trucky exclusive
    elif job_event_type == "job.cancelled":
        events.append({"location": None, "real_time": normalize_time(d["canceled_at"]), "time": time_seconds(d["canceled_at"]), "type": "job.cancelled", "meta": {"penalty": d["income"]}})
    if d["warp"] is None:
        d["warp"] = 1
    return {
        "object": "event",
        "type": job_event_type,
        "data": {
            "object": {
                "id": d["id"],
                "uuid": None, # not for trucky
                "object": "job",
                "driver": {
                    "steam_id": d["driver"]["steam_profile"]["steam_id"],
                    "username": d["driver"]["name"],
                    "profile_photo_url": d["driver"]["avatar_url"]
                },
                "start_time": normalize_time(d["started_at"]),
                "stop_time": normalize_time(d["completed_at"]) if d["completed_at"] is not None else normalize_time(d["canceled_at"]),
                "time_spent": d["real_driving_time_seconds"],
                "planned_distance": d["planned_distance_km"],
                "driven_distance": d["real_driven_distance_km"],
                "adblue_used": None, # not for trucky
                "fuel_used": d["fuel_used_l"],
                "is_special": d["special_job"],
                "is_late": d["late_delivery"],
                "market": d["market"],
                "cargo": {
                    "unique_id": d["cargo_id"],
                    "name": d["cargo_name"],
                    "mass": d["cargo_unit_count"] * d["cargo_unit_mass"],
                    "damage": round(d["cargo_damage"] / 100, 2),
                    "details": cargo_details
                },
                "game": {
                    "short_name": "eut2" if d["game"]["code"] == "ETS2" else "ats",
                    "language": None, # not for trucky
                    "timezone": d["timezone"], # trucky exclusive
                    "max_map_scale": d["max_map_scale"], # trucky exclusive
                    "had_police_enabled": d["realistic_settings"]["police"],
                    "realistic_settings": d["realistic_settings"] # trucky exclusive
                },
                "multiplayer": multiplayer,
                "source_city": {
                    "unique_id": d["source_city_id"],
                    "name": d["source_city_name"]
                },
                "source_company": {
                    "unique_id": d["source_company_id"],
                    "name": d["source_company_name"]
                },
                "destination_city": {
                    "unique_id": d["destination_city_id"],
                    "name": d["destination_city_name"]
                },
                "destination_company": {
                    "unique_id": d["destination_company_id"],
                    "name": d["destination_company_name"]
                },
                "truck": {
                    "unique_id": d["vehicle_in_game_id"],
                    "name": d["vehicle_model_name"],
                    "brand": {
                        "unique_id": d["vehicle_in_game_brand_id"],
                        "name": d["vehicle_brand_name"]
                    },
                    "odometer": d["vehicle_odometer_end_km"],
                    "initial_odometer": d["vehicle_odometer_start_km"],
                    "wheel_count": None, # not for trucky
                    "license_plate": None, # not for trucky
                    "license_plate_country": None, # not for trucky
                    "current_damage": None, # not for trucky
                    "total_damage": {
                        "all": round(d["vehicle_damage"] / 100, 2)
                    },
                    "top_speed": round(d["max_speed_kmh"] / 3.6, 2),
                    "average_speed": round(d["average_speed_kmh"] / 3.6, 2)
                },
                "trailers": [
                    {
                        "name": d["trailer_name"],
                        "unique_id": d.get("trailer_in_game_id"),
                        "body_type": d["trailer_body_type"],
                        "chain_type": d["trailer_chain_type"],
                        "wheel_count": None, # not for trucky
                        "brand": None, # not for trucky
                        "license_plate": None, # not for trucky
                        "license_plate_country": None, # not for trucky
                        "current_damage": None, # not for trucky
                        "total_damage": {
                            "all": round(d["trailers_damage"] / 100, 2)
                        }
                    }
                ],
                "events": events,
                "mods": [],
                "warp": d["warp"] # trucky exclusive
            }
        }
    }


async def post_update(response: Response, request: Request):
    app = request.app
    if "trucky" not in configured_trackers(app):
        response.status_code = 404
        return {"error": "Not Found"}
    dhrid = request.state.dhrid
    await app.db.new_conn(dhrid, db_name = app.config.db_name)

    webhook_signature = request.headers.get('X-Signature-SHA256')

    ip_ok = False
    needs_validate = False
    for tracker in app.config.trackers:
        if tracker["type"] != "trucky":
            continue
        if type(tracker["ip_whitelist"]) == list and len(tracker["ip_whitelist"]) > 0:
            needs_validate = True
            if request.client.host in tracker["ip_whitelist"]:
                ip_ok = True
    if needs_validate and not ip_ok:
        response.status_code = 403
        await AuditLog(request, -999, "tracker", ml.ctr(request, "rejected_tracker_webhook_post_ip", var = {"tracker": "Trucky", "ip": request.client.host}))
        return {"error": "Validation failed."}

    raw_body = await request.body()
    raw_body_str = raw_body.decode("utf-8")

    if request.headers.get("Content-Type") == "application/x-www-form-urlencoded":
        d = parse_qs(raw_body_str)
    elif request.headers.get("Content-Type") == "application/json":
        d = json.loads(raw_body_str)
    else:
        response.status_code = 400
        return {"error": "Unsupported content type."}
    sig_ok = False
    needs_validate = False # if at least one tracker has webhook secret, then true (only false when all doesn't have webhook secret)
    for tracker in app.config.trackers:
        if tracker["type"] != "trucky":
            continue
        if tracker["webhook_secret"] is not None and tracker["webhook_secret"] != "":
            needs_validate = True
            sig = hmac.new(tracker["webhook_secret"].encode(), msg=raw_body, digestmod=hashlib.sha256).hexdigest()
            if webhook_signature is not None and hmac.compare_digest(sig, webhook_signature):
                sig_ok = True
    if needs_validate and not sig_ok:
        response.status_code = 403
        await AuditLog(request, -999, "tracker", ml.ctr(request, "rejected_tracker_webhook_post_signature", var = {"tracker": "Trucky", "ip": request.client.host}))
        return {"error": "Validation failed."}

    if d["event"] in ("user_joined_company", "user_left_company", "user_role_changed", "user_updated"):
        # Re-check the authoritative identity rather than trusting webhook role data.
        from functions.trucky_identity import lookup_member, sync_user
        profile = d.get("data", {}).get("steam_profile") or {}
        steamid = profile.get("steam_id")
        if steamid:
            await app.db.execute(dhrid, "SELECT uid FROM user WHERE steamid=%s", (str(steamid),))
            linked = await app.db.fetchone(dhrid)
            if linked:
                for tracker in app.config.trackers:
                    if tracker["type"] == "trucky" and tracker.get("api_token") and tracker.get("company_id"):
                        member = await lookup_member(app, dhrid, tracker, steamid)
                        await sync_user(request, linked[0], tracker, member)
        return Response(status_code=204)
    if d["event"] in ("job_created", "job_event_created"):
        # The company reconciliation refreshes in-progress jobs every 90 seconds.
        return Response(status_code=204)

    original_data = copy.deepcopy(d)
    converted_data = convert_format(copy.deepcopy(d))
    if converted_data is None:
        response.status_code = 400
        return {"error": "Only job_completed, job_canceled and user_joined_company events are accepted."}

    result = await handle_new_job(request, original_data, converted_data, "trucky", allow_external_driver = True)
    if len(result) == 2:
        response.status_code = result[0]
        return {"error": result[1]}

    return Response(status_code = 204)

async def post_import(response: Response, request: Request, jobid: int, authorization: str = Header(None), bypass_tracker_check: Optional[bool] = False):
    app = request.app
    dhrid = request.state.dhrid
    rl = await ratelimit(request, 'POST /trucky/import', 60, 60)
    if rl[0]:
        return rl[1]
    for k in rl[1].keys():
        response.headers[k] = rl[1][k]

    await app.db.new_conn(dhrid, db_name = app.config.db_name)

    au = await auth(authorization, request, allow_application_token = True, required_permission = ["administrator", "import_dlogs"])
    if au["error"]:
        response.status_code = au["code"]
        del au["code"]
        return au

    try:
        r = await arequests.get(app, f"https://e.truckyapp.com/api/v1/job/{jobid}", dhrid = dhrid)
        if r.status_code != 200:
            d = r.json()
            response.status_code = r.status_code
            if "message" in d.keys():
                return {"error": d["message"]}
            else:
                return {"error": ml.tr(request, "unknown_error")}
        job_data = r.json()
    except:
        response.status_code = 503
        return {"error": ml.tr(request, 'service_api_error', var = {'service': 'Trucky'})}

    try:
        r = await arequests.get(app, f"https://e.truckyapp.com/api/v1/job/{jobid}/events", dhrid = dhrid)
        if r.status_code != 200:
            d = r.json()
            response.status_code = r.status_code
            if "message" in d.keys():
                return {"error": d["message"]}
            else:
                return {"error": ml.tr(request, "unknown_error")}
        events_data = r.json()
        job_data["events"] = events_data
    except:
        response.status_code = 503
        return {"error": ml.tr(request, 'service_api_error', var = {'service': 'Trucky'})}

    d = {"event": f"job_{job_data['status']}", "data": job_data}
    original_data = copy.deepcopy(d)
    converted_data = convert_format(copy.deepcopy(d))
    if converted_data is None:
        response.status_code = 400
        return {"error": "Only job_completed and job_canceled events are accepted."}

    result = await handle_new_job(request, original_data, converted_data, "trucky", bypass_tracker_check = bypass_tracker_check, allow_external_driver = True, historical = True)
    if len(result) == 2:
        response.status_code = result[0]
        return {"error": result[1]}

    return {"logid": result[0]}

async def put_driver(response: Response, request: Request, userid: int, authorization: str = Header(None)):
    app = request.app
    if "trucky" not in configured_trackers(app):
        response.status_code = 404
        return {"error": "Not Found"}
    dhrid = request.state.dhrid
    rl = await ratelimit(request, 'PUT /trucky/driver', 60, 60)
    if rl[0]:
        return rl[1]
    for k in rl[1].keys():
        response.headers[k] = rl[1][k]

    await app.db.new_conn(dhrid, db_name = app.config.db_name)

    au = await auth(authorization, request, allow_application_token = True, required_permission = ["administrator", "update_roles"])
    if au["error"]:
        response.status_code = au["code"]
        del au["code"]
        return au

    userinfo = await GetUserInfo(request, userid = userid, is_internal_function = True)
    if userinfo["uid"] is None:
        response.status_code = 404
        return {"error": ml.tr(request, "user_not_found", force_lang = au["language"])}

    tracker_app_error = await add_driver(request, userinfo["steamid"], au["uid"], userid, userinfo["name"], trackers = ["trucky"])

    if tracker_app_error == "":
        return Response(status_code=204)
    else:
        response.status_code = 503
        return {"error": tracker_app_error}

async def delete_driver(response: Response, request: Request, userid: int, authorization: str = Header(None)):
    app = request.app
    if "trucky" not in configured_trackers(app):
        response.status_code = 404
        return {"error": "Not Found"}
    dhrid = request.state.dhrid
    rl = await ratelimit(request, 'PUT /trucky/driver', 60, 60)
    if rl[0]:
        return rl[1]
    for k in rl[1].keys():
        response.headers[k] = rl[1][k]

    await app.db.new_conn(dhrid, db_name = app.config.db_name)

    au = await auth(authorization, request, allow_application_token = True, required_permission = ["administrator", "update_roles"])
    if au["error"]:
        response.status_code = au["code"]
        del au["code"]
        return au

    userinfo = await GetUserInfo(request, userid = userid, is_internal_function = True)
    if userinfo["uid"] is None:
        response.status_code = 404
        return {"error": ml.tr(request, "user_not_found", force_lang = au["language"])}

    tracker_app_error = await remove_driver(request, userinfo["steamid"], au["uid"], userid, userinfo["name"], trackers = ["trucky"])

    if tracker_app_error == "":
        return Response(status_code=204)
    else:
        response.status_code = 503
        return {"error": tracker_app_error}
