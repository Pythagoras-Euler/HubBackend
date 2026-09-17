# Personal charts, banner layers and TruckersHub routes

Personal profile charts show a chosen 7/30/90/365-day UTC period, including today. Older activity stays in the lifetime summary. Yearly charts use five-day buckets. Zero-activity charts display a concise empty state; zero is not treated as missing data.

Banner backgrounds and company logos load independently. A missing logo does not prevent the background from rendering, and a missing background never enlarges the logo into a background. Both images preserve their aspect ratio. The opacity applies only to the background. Template and completed-banner cache keys include the inputs, so changing the background URL does not reuse the previous template. The existing separate uploaded site banner can be used at `/api/client/assets/banner`.

## TruckersHub route enrichment

Official API reference: https://docs.truckershub.in/ (authentication, jobs and routes sections).

TruckersHub's desktop client can record routes. This integration adds its recorded route to an existing Hub delivery, including Trucky deliveries. It does not switch the job tracker or import duplicate jobs. To enable it, an administrator enters the company API Token from TruckersHub **Integrations → API** into Hub **Configuration → Tracker → TruckersHub routes**. Saving validates the token with `GET /v1/me`. The token is stored server-side and never returned by the settings endpoint. An empty token disables enrichment.

The worker checks at 15-minute intervals (first check on the next normal worker cycle), fetches company jobs for months containing local deliveries without telemetry, and reads `GET /v1/routes/{jobID}` for unique matches. Matching requires the same Steam ID, game, cargo ID, source/destination city and company IDs, and start/end times within 120 seconds. Both sides must be unambiguous. Only finite recorded coordinates are stored; existing telemetry is never overwritten. This preserves delivery identities, money, statistics and Public IDs.

The Trucky and TruckersHub clients must have recorded the same transport for cross-provider matching. Installing TruckersHub today cannot reconstruct routes for old Trucky-only jobs. The current Trucky API offers event locations, not a full recorded route. When complete telemetry exists, the delivery map displays it; otherwise the existing event markers remain. These limitations belong in documentation, not long explanations on delivery pages.

No TruckersHub API Token was available during this deployment. Provider matching/telemetry insertion is covered by fixtures based on the official schema; live authenticated route retrieval must be verified after the administrator configures the token.
