import apis.tracker.active as active
# Copyright (C) 2022-2026 CharlesWithC All rights reserved.
# Author: @CharlesWithC

from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

import apis.tracker.tracksim as tracksim
import apis.tracker.trucky as trucky
import apis.tracker.truckershub as truckershub
import apis.tracker.trucky_history as trucky_history
import apis.tracker.trucky_roles as trucky_roles
import apis.tracker.custom as custom
import apis.tracker.unitracker as unitracker

routes_tracksim = [
    APIRoute("/tracksim/update", tracksim.post_update, methods=["POST"], response_class=JSONResponse),
    APIRoute("/tracksim/driver/{userid}", tracksim.put_driver, methods=["PUT"], response_class=JSONResponse),
    APIRoute("/tracksim/driver/{userid}", tracksim.delete_driver, methods=["DELETE"], response_class=JSONResponse)
]

routes_tracksim_route = [
    APIRoute("/tracksim/update/route", tracksim.post_update_route, methods=["POST"], response_class=JSONResponse)
]

routes_trucky = [
    APIRoute("/deliveries/active/{provider}/{sourceid}/abandon", active.abandon, methods=["POST"], response_class=JSONResponse),
    APIRoute("/deliveries/active", active.get_jobs, methods=["GET"], response_class=JSONResponse),
    APIRoute("/deliveries/drivers", trucky_history.get_drivers, methods=["GET"], response_class=JSONResponse),
    APIRoute("/truckershub/retry", truckershub.post_retry, methods=["POST"], response_class=JSONResponse),
    APIRoute("/truckershub/update", truckershub.post_update, methods=["POST"], response_class=JSONResponse),
    APIRoute("/truckershub/settings", truckershub.get_settings, methods=["GET"], response_class=JSONResponse),
    APIRoute("/truckershub/settings", truckershub.put_settings, methods=["PUT"], response_class=JSONResponse),
    APIRoute("/truckershub/routes/settings", truckershub.get_settings, methods=["GET"], response_class=JSONResponse),
    APIRoute("/truckershub/routes/settings", truckershub.put_settings, methods=["PUT"], response_class=JSONResponse),
    APIRoute("/trucky/role-mappings", trucky_roles.get_mappings, methods=["GET"], response_class=JSONResponse),
    APIRoute("/trucky/role-mappings/{companyid}/{roleid}", trucky_roles.put_mapping, methods=["PUT"], response_class=JSONResponse),
    APIRoute("/trucky/active", active.get_jobs, methods=["GET"], response_class=JSONResponse),
    APIRoute("/trucky/drivers", trucky_history.get_drivers, methods=["GET"], response_class=JSONResponse),
    APIRoute("/trucky/sync-status", trucky_history.get_sync_status, methods=["GET"], response_class=JSONResponse),
    APIRoute("/trucky/update", trucky.post_update, methods=["POST"], response_class=JSONResponse),
    APIRoute("/trucky/import/{jobid}", trucky.post_import, methods=["POST"], response_class=JSONResponse),
    APIRoute("/trucky/driver/{userid}", trucky.put_driver, methods=["PUT"], response_class=JSONResponse),
    APIRoute("/trucky/driver/{userid}", trucky.delete_driver, methods=["DELETE"], response_class=JSONResponse)
]

routes_custom = [
    APIRoute("/custom-tracker/update", custom.post_update, methods=["POST"], response_class=JSONResponse),
]

routes_unitracker = [
    APIRoute("/unitracker/update", unitracker.post_update, methods=["POST"], response_class=JSONResponse),
]
