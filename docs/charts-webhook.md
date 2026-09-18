# TruckersHub route notifications

The optional TruckersHub integration supplements routes on existing deliveries. It does not import TruckersHub jobs as a separate tracker. Trucky remains the configured job source.

Save the TruckersHub company API token in administrator settings, then copy the generated **TruckersHub Webhook URL** to TruckersHub Integrations. The URL includes a derived webhook credential (not the API token); treat it as private. Changing or removing the API token invalidates the old URL.

POST notifications return HTTP 200 promptly. They invalidate the route reconciliation cooldown, coalescing repeats for 90 seconds. The existing worker fetches authoritative jobs/routes from the authenticated company API and matches deliveries conservatively. Payload fields never create deliveries or assign drivers. A successful notification acknowledges scheduling, not completion of a route match; inspect synchronization status afterward. Periodic polling also remains enabled.

Both bare objects/arrays and `data` response envelopes are supported. Error envelopes are rejected without exposing upstream body text or saving an invalid token. The actual company token must still be re-saved and verified after deployment; its earlier validation failure left no token in production.

Reference: https://docs.truckershub.in/ (POST, HTTP 200 acknowledgement, five-second timeout).

# Charts

Overview, statistics, profiles and application charts default to 30 days, with 7/30/90/365-day presets, UTC buckets including today, period totals and concise empty states. Yearly charts use five-day buckets to remain below the 100-bucket API limit. Statistics retain driver and custom date filters. New-driver charts count joins in the period, not the lifetime driver total. Application charts display no-applications states rather than no-transport states.
