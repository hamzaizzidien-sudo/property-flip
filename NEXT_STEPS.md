# Where we are (2026-10-25)

Credits used: 20 of 500 (ledger: `python -m flipwatch.cli credits`). Reference DB (Land Registry,
postcodes) is git-ignored: rebuild with `python -m flipwatch.cli load-sold-prices 2021 2022 2023 2024 2025 2026`.

## Findings that change the design
- **Valuations overstated.** GDV v0 uses Land Registry comps without size/bedrooms. Pilot on Burland
  Road (L26 9YJ, 721 sq ft): v0 GDV £255k; size-matched £/sqft comps (£253–312) and PropertyData
  valuation at high finish (£240k ±20k) give ~£210k / £220k / £235k → profit at asking ~£4.6k,
  max offer for £40k = £136.5k (WATCH, not INVESTIGATE). Fix: £/sqft model with floor areas.
- **Coverage truncation.** The combined 5-list sourced-properties query returns only ~198 results
  (to 6.7 mi) regardless of radius. Single-list queries go further: unmodernised 200–290 within
  15 mi (#200 at 12.2 mi), auction houses 100–190, reduced / slow-to-sell / back-on-market <190 each.
  Empty pages cost 0 credits, so page probes size a query for free. Scheduled runs must use
  per-list queries; current RUN_PROFILES (combined, 8 mi) are wrong. Scheduled tasks exist but have
  no PropertyData connector attached yet, so they cannot spend credits.
- `max_age` = days since last listing update (new/reduced/relisted), not listing age.

## Session 2 (2026-10-25): EPC code written, not yet run
- The old EPC host (epc.opendatacommunities.org) now 301s to the MHCLG service. Its API is
  `https://api.get-energy-performance-data.communities.gov.uk` with `Authorization: Bearer $EPC_API_KEY`
  (EPC_EMAIL no longer used). **This environment's network policy blocks that host**, so the key
  has not been verified yet. Allow it under the environment's Network access → Allowed domains.
- Written and unit-tested (with fixtures, not real data): `sources/epc.py` (postcode search cached
  in `epc_certificates`, full certificate fetched only for addresses that have a Land Registry sale,
  matched by postcode + house number + street), `comps.gdv_per_sqft` (±25%/±35% size match,
  weighted 50/70/85th £/sqft x subject sqft, MEDIUM at n>=8 and spread <=20%),
  `finance.refurb_estimate` (£35/sqft modernise, £55/sqft derelict, min £15k, £25k if no sqft),
  `cli enrich-epc`, `cli deal-sheets`. Without floor areas `screen` falls back to v0 unchanged.
- Subjects with no listed sqft and no house number get a median-of-nearby-EPCs estimate and LOW confidence.

## Next steps
1. With the host allowed: one EPC lookup to verify the key, then
   `enrich-epc` → `screen` → `shortlist` → `deal-sheets`, and check Burland Road lands near
   ~£210k / £220k / £235k (the size-matched pilot). Tune bands/percentiles if it does not.
2. Switch RUN_PROFILES / `plan` to one list per query (see coverage finding) before giving the
   scheduled tasks a PropertyData connector.
3. Awaiting user decision: full 15-mile per-list sweep (~70–110 credits) and ongoing option
   (15 mi ~55–60/week, or monthly 15-mi sweep + daily 8-mi changes ~30/week); optional PropertyData
   comps + valuation (2 credits/property) for the final shortlist.
