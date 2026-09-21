# TruckersHub tracker

TruckersHub is a standalone delivery tracker (type 6), alongside Trucky,
TrackSim and UniTracker. Administrators add it in the tracker configuration
list; users can select it in personal settings, and staff can assign it during
admission or in bulk. Save/reload the tracker list to publish that choice.
Selecting a tracker does not enroll a driver in an external company.

In the TruckersHub card, save the company API token. This validates `/v1/me`
and stores the token privately. Copy the generated webhook URL into TruckersHub.
Its verification value is derived from the token; it is not the API token.
The settings API is `/truckershub/settings`; the former
`/truckershub/routes/settings` remains a compatibility alias.

The independent worker polls every 60 seconds after each pass and imports
company jobs from September 2025, newest month first. Historical months are
cached for 24 hours; a durable webhook receipt invalidates that shortcut.
The webhook commits its receipt before returning HTTP 200. Jobs are fetched
again through the authenticated company API rather than trusting webhook fields.
Bounded batches resume on subsequent passes. Invalid jobs are retained for retry.

Imports use the common delivery writer and Public ID allocator. Steam identity
determines ownership; jobs from drivers not yet registered remain external and
are relinked when the matching Hub account has driver permission. This grants
no roles. Like Trucky reconciliation, imports use historical mode: no replay
of rewards, challenge credits, economy payments or delivery notifications.

Provider/source ID and driver advisory locks prevent concurrent duplicate
inserts. Strong Trucky/TruckersHub matches become aliases of one delivery and
one Public ID. Uncertain matches require administrator review: link an existing
Public ID or explicitly confirm a separate delivery. Deleted jobs are not
resurrected. Original provider payloads are stored privately, not in public API
responses. Processed webhook receipts are retained for 30 days.

Truck/trailer, actual start/end, cargo, fuel and income are normalized from the
documented API. Speed is m/s, distance km; income, not THP, is delivery revenue.
Missing optional values stay unknown. Active jobs are separate from completed
statistics. Live access may depend on provider subscription capabilities;
failure of the live endpoint does not prevent history import. Routes are fetched
independently and attached where supplied; a missing route never blocks a job.

## Deployment and verification

Run `maintenance/migrate-truckershub-import.py` in the backend container before
starting the new worker. The migration only creates `delivery_source` and
`tracker_inbox`; it does not rewrite existing deliveries. Fresh database setup
also includes these tables. Old backend images can ignore the additive tables
when rolling back.

Tests cover conversion, webhook authorization and durable receipt, worker
retries, source deduplication and user tracker selection. The isolated MariaDB
integration script exercises the real delivery writer with disposable tables,
including concurrent retries and deleted-job protection. Browser fixtures cover
admin retry and live cards. No real TruckersHub company import was performed:
the operator has no API token and explicitly deferred that validation.

Provider contract: https://docs.truckershub.in/ . Real payload compatibility,
rate limits and company-specific subscription access remain to be confirmed
when a token becomes available.
