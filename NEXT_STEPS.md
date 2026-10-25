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

## Session 2 (2026-10-25): EPC floor areas live, £/sqft valuations, deal sheets
- EPC API: `https://api.get-energy-performance-data.communities.gov.uk`, `Authorization: Bearer
  $EPC_API_KEY` (EPC_EMAIL unused). Host allowed in the environment's network policy; key verified.
- `enrich-epc` for the 32 non-REJECT subjects: 2,767 postcodes, 77,464 certificates listed,
  8,246 fetched, 6,280 Land Registry sales now carry a floor area (~1.5 h; cached in reference.db,
  which is git-ignored, so a fresh session must re-run it).
- Burland Road check: £206k / £223k / £223k, MEDIUM, max offer £139k → WATCH (pilot ~£210/220/235k).
- Shortlist now 25 (1 CALL AGENT, 11 INVESTIGATE, 13 WATCH); deal sheets in `output/deals/`.
- 15 of 25 have no listed floor area: estimated as median of nearby same-type EPCs, capped by beds
  (1:600, 2:800, 3:1000, 4+:1300 sq ft), confidence LOW. Auctions assume guide + 25%; the £45k/£50k
  guides (Dragon Lane, Grange Park Road) are almost certainly too optimistic.
- Refurb: £35/sqft modernise, £55/sqft derelict, min £15k. Opening offer never above asking.

## Next steps
1. Get real floor areas (floorplan / EPC by house number) for the estimated-size INVESTIGATEs and
   re-screen; optionally PropertyData valuation (2 credits each) on the final few — ask first.
2. Switch RUN_PROFILES / `plan` to one list per query (see coverage finding) before giving the
   scheduled tasks a PropertyData connector.
3. Awaiting user decision: full 15-mile per-list sweep (~70–110 credits) and ongoing option
   (15 mi ~55–60/week, or monthly 15-mi sweep + daily 8-mi changes ~30/week); optional PropertyData
   comps + valuation (2 credits/property) for the final shortlist.
