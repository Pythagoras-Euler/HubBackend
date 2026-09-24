# Unfinished transports

`active_delivery` persists Trucky and TruckersHub live snapshots independently of completed deliveries and statistics. The additive table is prepared by `tracker_schema.prepare`; run the migration before starting an upgraded server.

A minute worker reconciles source IDs against completed (including explicitly deleted) deliveries. Matching across different IDs requires the same Steam ID, game, cargo, departure and destination city/company, and start times within 120 seconds. Missing identity fields or ambiguous completed matches are not merged. Duplicate entries are hidden from the active list and retained as closed records, with the matching delivery ID when available. Upstream jobs are never deleted.

An owner with a linked Steam ID or staff with `administrator` / `delete_dlogs` can abandon a transport through `POST /deliveries/active/{provider}/{sourceid}/abandon` with `confirmed: true`. The frontend requires a second confirmation. This changes Hub state only; source refreshes cannot reopen it. Real subsequent completed deliveries can still import normally. Close time and actor are retained.

`active_delivery_timeout_days` defaults to 7, accepts integers 1 through 365, and is editable in tracker configuration. Age is measured from transport start (first observation only when start is unknown). The worker checks every minute. Expired tasks become `aborted`, including tasks no longer returned by the source. Neither abandonment nor timeout creates an artificial delivery or changes completed statistics.

`GET /deliveries/active` returns active records, or closed records with `include_closed=true`; responses include server-calculated `can_abandon`. Legacy `/trucky/active` remains an alias. Legacy snapshots without a game field wait for the upgraded provider feed before reconciliation.

Route labels follow actual telemetry and fetch state. TrackSim can supply route telemetry and supports route reload. TruckersHub fetches routes separately from history import, retrying unavailable routes. A provider HTTP 403 is shown as restricted, never as an available full route. Current company access returned insufficient boosts; history import remains usable. Event markers alone are not a full driven route. Trucky jobs can acquire routes through linked sources that supply them.

## TruckersHub route permission switch

Set `"truckershub_route_access": false` in the backend runtime config (default). While false, Hub makes no TruckersHub route requests and schedules no route retries; previously saved routes remain visible. Set it to the boolean `true` only after enabling the company route entitlement. History import and other trackers remain independent. Restart/reload config after editing the file.

## Event locations without route access

TruckersHub job events remain available independently of the paid route API. The converter preserves supplied start/end/cancellation and intermediate event coordinates, accepts X/Z with optional Y, and rejects zero placeholders. The event map takes precedence over missing/disabled route labels, showing isolated points. The coordinate backfill changes only locations in existing event records, preserving financial data and statistics.

Full audit of 233 deliveries: 9 TruckersHub jobs have all 9 destination locations and 7 valid starts (2 upstream zero placeholders). All 224 Trucky job details and event feeds were individually checked, with no job-level coordinates or departure/arrival events returned. Intermediate Trucky event coordinates remain usable; they must not be relabeled as actual departure/arrival points.
