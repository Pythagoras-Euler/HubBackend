"""Public fields for unfinished Trucky jobs; never counted as completed deliveries."""
from trucky_time import normalize_time


def active_job(data):
    if data.get("status") != "in_progress":
        return None
    driver = data.get("driver") or {}
    return {
        "trackerid": int(data["id"]), "status": "in_progress",
        "driver": driver.get("name"),
        "source": {"city": data.get("source_city_name"), "company": data.get("source_company_name")},
        "destination": {"city": data.get("destination_city_name"), "company": data.get("destination_company_name")},
        "cargo": data.get("cargo_name"), "distance": data.get("real_driven_distance_km") or data.get("driven_distance_km"),
        "start_time": normalize_time(data.get("started_at")), "stop_time": None,
        "truck": {"name": data.get("vehicle_model_name"), "brand": {"name": data.get("vehicle_brand_name")}, "unique_id": data.get("vehicle_in_game_id")},
        "trailers": [{"name": data.get("trailer_name"), "unique_id": data.get("trailer_in_game_id"), "body_type": data.get("trailer_body_type")}],
    }
