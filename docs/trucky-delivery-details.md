# Trucky delivery details

Verified on 2026-09-17 against the [official OpenAPI](https://e.truckyapp.com/openapi)
and the configured company's API, using field names, types and aggregate counts only.

- All 214 stored Trucky deliveries have `start_time` and `stop_time`. The frontend
  shows both in the user's display timezone, using the offset at the delivery date.
  New imports normalize ISO timestamps correctly, including fractional seconds and
  explicit UTC offsets. Cancellation time remains the end time for canceled jobs.
- The two special deliveries have empty source/destination company names and IDs
  upstream. The frontend preserves actual names/IDs where present and otherwise
  labels the special transport depot as unnamed. It does not invent depot names.
- Stored fine metadata and checked upstream fine events contain `offence` and
  `amount`, without speed/limit. Missing values display “Not provided”; supplied
  numeric speeds retain the existing normalized m/s conversion to km/h or mph.
- Trucky's public job and event APIs do not expose a full driven route. Event
  `x`/`z` positions are shown as separate markers, never a connected route.
  TrackSim route refresh remains exclusive to TrackSim. Existing telemetry routes
  take precedence over event markers; legacy v1 routes are decoded numerically.
- Map metadata lacks CORS permission for the Hub origin. `GET
  /api/map/info/{game}/{variant}` fetches only the five fixed map metadata files
  from `map.charlws.com`, validates bounds and caches for one hour. It accepts no
  arbitrary URL. Unsupported maps return 404; upstream failure returns 502.
  `/api` is the deployment's configured API prefix.

No database migration or history rewrite is required for these display fixes.
All existing internal, public and external IDs and stored payloads are preserved.
