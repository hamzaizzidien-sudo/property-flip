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

## Next steps
1. EPC: `EPC_API_KEY` (and `EPC_EMAIL` if the key is from epc.opendatacommunities.org) should now be
   in the environment. Verify with one lookup (never print the key), then join EPC floor areas to
   `sold_prices` by postcode + address, and rebuild `comps.py` on £/sqft of size-matched sales
   (refurbished value = upper part of same-type £/sqft × subject sqft). Refurb cost should scale with
   size and condition instead of a flat £25k.
2. Re-screen and produce one deal sheet per shortlisted property (layout as the Burland Road example:
   comps table, after-works low/base/high, itemised costs, profit at asking, max offer, portal link).
3. Awaiting user decision: full 15-mile per-list sweep (~70–110 credits) and ongoing option
   (15 mi ~55–60/week, or monthly 15-mi sweep + daily 8-mi changes ~30/week); optional PropertyData
   comps + valuation (2 credits/property) for the final shortlist.
