"""Command line entry point:  python -m flipwatch.cli <command> ..."""
import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from . import comps as comps_mod
from . import finance, notes, scoring
from .config import RUN_PROFILES, settings
from .db import (Analysis, ApiCall, Comparable, ListingObservation, ListingSource, Property, PropertyEvent,
                 RawResponse, Session, init_db)
from .ingest import import_raw_file
from .sources import propertydata


def cmd_discover(args):
    """Paid discovery via REST. Prints the estimate; spends nothing without --confirm."""
    sc = settings.search
    params = dict(list=",".join(args.lists.split(",") if args.lists else sc.priority_lists),
                  location=sc.location, radius=args.radius or sc.radius_miles,
                  max_price=args.max_price or sc.max_price, standardised_type=sc.standardised_type,
                  exclude_sstc=sc.exclude_sstc, results=args.results, page=args.page)
    est = propertydata.CREDIT_COST["sourced-properties"](params)
    print(f"sourced-properties {json.dumps(params)}\n  estimated cost: {est} credit(s); "
          f"run limit {settings.max_credits_per_run}")
    if not args.confirm:
        print("  dry run — add --confirm to spend credits")
        return
    client = propertydata.PropertyDataClient()
    data, _ = client.get("sourced-properties", params)
    path = propertydata.save_raw("sourced-properties", params, data, via="rest", tag=f"p{args.page}")
    res = import_raw_file(path)
    print(f"  saved {path.name}: {res}")
    if res["page_full"]:
        print("  page was full — more results probably exist. Stopping; re-run with --page to continue.")


def cmd_import(args):
    for f in args.files:
        print(f, import_raw_file(Path(f)))


def _latest_analysis(s, pid):
    return s.scalar(select(Analysis).where(Analysis.property_id == pid).order_by(Analysis.id.desc()))


def _latest_obs(s, pid):
    return s.scalar(select(ListingObservation).where(ListingObservation.property_id == pid)
                    .order_by(ListingObservation.observed_at.desc()))


def _rows(s):
    rows = []
    for p in s.scalars(select(Property)):
        o = _latest_obs(s, p.id)
        hist = o.price_history or []
        comp = scoring.screening_components(o.days_on_market, hist, p.current_asking_price,
                                            p.motivation_flags or [], p.risk_flags or [])
        a = s.scalar(select(Analysis).where(Analysis.property_id == p.id).order_by(Analysis.analysis_date.desc()))
        rows.append(dict(
            id=p.id, source_ref=p.propertydata_id, address=p.precise_address or p.address, postcode=p.postcode,
            type=p.property_type, type_raw=p.property_type_raw, beds=p.bedrooms, sqft=p.floor_area_sqft,
            status=p.current_status, asking=p.current_asking_price, original_asking=p.original_asking_price,
            first_listed=p.first_listed_date, days_on_market=o.days_on_market,
            days_since_price_change=o.days_since_price_change,
            n_reductions=sum(1 for x, y in zip(hist, hist[1:]) if y["price"] < x["price"]),
            total_reduction_pct=round(scoring.total_reduction_pct(hist, p.current_asking_price), 1),
            reduced_by_pct_reported=o.reduced_by_pct,
            price_history=" > ".join(f"{h['date']}:£{h['price']:,}" for h in hist),
            lists="|".join(p.current_lists or []), motivation="|".join(p.motivation_flags or []),
            risks="|".join(p.risk_flags or []), screening_score=sum(comp.values()),
            **{f"s_{k}": v for k, v in comp.items()},
            gdv_needed_at_asking=finance.required_gdv(p.current_asking_price) if p.current_asking_price else None,
            expected_price=a.expected_purchase_price if a else None,
            gdv_low=a.gdv_low if a else None, gdv_base=a.gdv_base if a else None,
            gdv_high=a.gdv_high if a else None, gdv_basis=a.gdv_source if a else None,
            confidence=a.confidence if a else None,
            profit_at_expected=a.expected_profit_at_asking if a else None,
            max_purchase=a.maximum_purchase_price_for_target if a else None,
            opening_offer=a.opening_offer if a else None,
            opportunity_score=round(a.opportunity_score, 1) if a else None,
            offer_gap=a.offer_gap if a else None, recommendation=a.recommendation if a else None,
            url=p.portal_url))
    return sorted(rows, key=lambda r: -(r["opportunity_score"] if r["opportunity_score"] is not None
                                        else r["screening_score"]))


def cmd_report(args):
    with Session() as s:
        rows = _rows(s)
        calls = s.execute(select(func.count(), func.sum(ApiCall.credits_reported))).one()
        events = Counter(e for (e,) in s.execute(select(PropertyEvent.event_type)))
    out = settings.output_dir / "candidates.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} properties | API calls {calls[0]} | credits used {calls[1] or 0} | events {dict(events)}")
    print(f"{'id':>3} {'score':>5} {'ask':>8} {'orig':>8} {'dom':>4} {'red%':>5} {'type':<13}{'bd':>2}  address / flags")
    for r in rows:
        print(f"{r['id']:>3} {r['screening_score']:>5} {r['asking']:>8,} {r['original_asking'] or 0:>8,} "
              f"{r['days_on_market'] or 0:>4} {r['total_reduction_pct']:>5} {r['type']:<13}{r['beds'] or 0:>2}  "
              f"{r['address'][:45]}\n{'':>53}lists={r['lists']}  risks={r['risks'] or '-'}")
    print(f"CSV: {out}")


def cmd_analyse(args):
    """Record a deal appraisal with a manually supplied GDV."""
    with Session.begin() as s:
        p = s.get(Property, args.property_id)
        r, score, _ = record_analysis(s, p, args.asking or p.current_asking_price, args.gdv_low or args.gdv,
                                   args.gdv, args.gdv_high or args.gdv, args.refurb, args.confidence,
                                   args.gdv_source)
    print(json.dumps({**r, "opportunity_score": score}, indent=1))


def record_analysis(s, p, asking, gdv_low, gdv, gdv_high, refurb, confidence, gdv_source, extra=None):
    r = finance.appraise(asking, gdv_low, gdv, gdv_high, refurb, confidence, p.risk_flags or [],
                         p.motivation_flags or [])
    o = _latest_obs(s, p.id)
    comp = scoring.screening_components(o.days_on_market, o.price_history or [], asking,
                                        p.motivation_flags or [], p.risk_flags or [])
    score = (sum(comp.values()) + scoring.financial_points(r["offer_gap"], r["expected_purchase_price"])
             + scoring.CONFIDENCE_POINTS[confidence])
    d = r["at_asking"]
    prev = s.scalar(select(Analysis).where(Analysis.property_id == p.id).order_by(Analysis.analysis_date.desc()))
    a = Analysis(property_id=p.id, asking_price=asking, expected_purchase_price=r["expected_purchase_price"],
                 gdv_low=gdv_low, gdv_base=gdv,
                 gdv_high=gdv_high, gdv_source=gdv_source, refurb_cost=d["refurb"],
                 sdlt=d["sdlt"], legal_cost=d["legal"], finance_cost=d["finance"], holding_cost=d["holding"],
                 selling_cost=d["selling"], contingency=d["contingency"],
                 expected_profit_at_asking=d["net_profit"],
                 maximum_purchase_price_for_target=r["max_purchase_price"], offer_gap=r["offer_gap"],
                 opening_offer=r["opening_offer"], opportunity_score=score,
                 recommendation=r["recommendation"], confidence=confidence, risks=p.risk_flags,
                 assumptions={**vars(settings.flip), **(extra or {})})
    s.add(a)
    s.flush()
    in_range = r["offer_gap"] <= 0 and r["recommendation"] != "REJECT"
    was_in_range = prev is not None and prev.offer_gap is not None and prev.offer_gap <= 0 \
        and prev.recommendation != "REJECT"
    if in_range and not was_in_range:
        s.add(PropertyEvent(property_id=p.id, event_type="ENTERED_BUY_RANGE", event_data={"asking": asking}))
    if was_in_range and not in_range:
        s.add(PropertyEvent(property_id=p.id, event_type="LEFT_BUY_RANGE", event_data={"asking": asking}))
    if prev and prev.opportunity_score is not None and score - prev.opportunity_score >= 10:
        s.add(PropertyEvent(property_id=p.id, event_type="SCORE_UPGRADE",
                            event_data={"from": prev.opportunity_score, "to": score}))
    return r, score, a


def cmd_screen(args):
    """Value every active candidate from local Land Registry comps and record an appraisal (free).
    £/sqft of size-matched sales when floor areas exist (run enrich-epc first), else v0 price comps."""
    from .sources import epc
    with Session.begin() as s:
        props = [p for p in s.scalars(select(Property).where(Property.current_status == "ACTIVE"))
                 if not args.ids or p.id in args.ids]
        for p in props:
            if args.changed:
                last = _latest_analysis(s, p.id)
                if last and last.asking_price == p.current_asking_price:
                    continue
            beds = p.bedrooms or 3
            if (p.property_type in ("semi-detached", "detached") and beds <= 2) or \
                    (p.property_type == "terraced" and (beds >= 4 or beds <= 1)):
                p.risk_flags = sorted(set(p.risk_flags or []) | {"gdv_bedroom_mismatch"})
            sqft, sqft_src = epc.subject_floor_area(s, p)
            def enough(c):  # 8 size-matched sales, or 8 sales when none carry a floor area yet
                return len(comps_mod.size_matched(c, sqft, 0.35)) >= 8 if sqft and \
                    any(x["floor_area_sqft"] for x in c) else len(c) >= 8
            comps = comps_mod.find_comps(s, p, enough=enough)
            if not sqft and (sqft := comps_mod.estimated_sqft(comps)):
                sqft_src = "estimated from nearby EPCs"
            g = comps_mod.gdv_per_sqft(comps, sqft) if sqft else None
            if g:
                if sqft_src != "listing" and sqft_src != "epc":
                    g["confidence"] = "LOW"
                source = (f"£/sqft v1: n={g['n']} ±{g['size_band']:.0%} size, £{g['ppsf_low']}/{g['ppsf_base']}/"
                          f"{g['ppsf_high']} psf x {sqft} sqft ({sqft_src}) street={g['same_street']} "
                          f"spread={g['spread']}")
            else:
                g = comps_mod.gdv_from_comps(comps)
                if g:
                    source = f"LR comps v0: n={g['n']} street={g['same_street']} spread={g['spread']}"
            if not g:
                print(f"{p.id:>3} {p.address[:40]:<40} too few comps ({len(comps)})")
                continue
            refurb, refurb_basis = finance.refurb_estimate(sqft, p.motivation_flags or [])
            r, score, a = record_analysis(s, p, p.current_asking_price, g["gdv_low"], g["gdv_base"],
                                          g["gdv_high"], refurb, g["confidence"], source,
                                          extra=dict(subject_sqft=sqft, subject_sqft_source=sqft_src,
                                                     refurb_basis=refurb_basis))
            for c in g["kept"]:
                s.add(Comparable(subject_property_id=p.id, analysis_id=a.id, comparable_address=c["address"],
                                 sale_price=c["price"], sale_date=c["sale_date"], property_type=c["property_type"],
                                 floor_area_sqft=c.get("floor_area_sqft"),
                                 price_per_sqft=round(c["ppsf"], 1) if c.get("ppsf") else None,
                                 distance_miles=c["distance"], similarity_score=round(c["weight"], 3),
                                 source="land_registry+epc" if c.get("ppsf") else "land_registry"))
            print(f"{p.id:>3} {p.address[:40]:<40} ask £{p.current_asking_price:>7,} "
                  f"exp £{r['expected_purchase_price']:>7,} GDV £{g['gdv_base']:>7,} "
                  f"(n={g['n']}) max £{r['max_purchase_price']:>7,} gap £{r['offer_gap']:>8,} {r['recommendation']}")


def cmd_enrich_epc(args):
    """Fetch EPC floor areas for the comps around each subject (free; EPC_API_KEY bearer token).
    Default subjects: active properties whose latest appraisal is not REJECT."""
    from .sources import epc
    client = epc.EpcClient()
    with Session() as s:
        props = []
        for p in s.scalars(select(Property).where(Property.current_status == "ACTIVE")):
            a = _latest_analysis(s, p.id)
            if p.id in args.ids if args.ids else (a is None or a.recommendation != "REJECT"):
                props.append(p)
        wanted = defaultdict(set)  # postcode -> address keys needing a floor area
        for p in props:
            for c in comps_mod.find_comps(s, p, enough=lambda c: False):  # widest window
                wanted[c["postcode"]].add(epc.address_key(c["address"]))
            if p.postcode and p.precise_address:
                wanted[p.postcode].add(epc.address_key(p.precise_address.split(",")[0]))
    print(f"{len(props)} subjects, {len(wanted)} postcodes", flush=True)
    searched = fetched = 0
    for i, (pc, keys) in enumerate(sorted(wanted.items()), 1):
        with Session.begin() as s:
            searched += epc.search_postcode(s, client, pc)
            fetched += epc.fetch_floor_areas(s, client, pc, keys)
        if i % 25 == 0:
            print(f"  {i}/{len(wanted)} postcodes, {searched} certificates listed, {fetched} fetched", flush=True)
    with Session.begin() as s:
        matched = epc.match_sales(s, set(wanted))
    print(f"done: {searched} certificates listed, {fetched} fetched, {matched} sales now have a floor area")


def cmd_load_sold(args):
    """Load HM Land Registry Price Paid yearly files into sold_prices (free)."""
    from .sources.landregistry import load_year
    for y in args.years:
        print(y, load_year(y), flush=True)


def cmd_credits(args):
    with Session() as s:
        for c in s.scalars(select(ApiCall).order_by(ApiCall.called_at)):
            print(c.called_at, c.endpoint, c.via, c.credits_reported, c.result_count)


# ---------------------------------------------------------------- scheduled runs (via MCP connector)

def _run_params(kind: str, page: int) -> dict:
    sc, prof = settings.search, RUN_PROFILES[kind]
    params = dict(list=",".join(sc.priority_lists), location=sc.location, radius=prof.radius_miles,
                  max_price=sc.max_price, standardised_type=sc.standardised_type, exclude_sstc=sc.exclude_sstc,
                  results=prof.results_per_page, page=page)
    if prof.max_age:
        params["max_age"] = prof.max_age
    return params


def _run_credits(s, run_id: str) -> int:
    return s.scalar(select(func.coalesce(func.sum(ApiCall.credits_reported), 0)).where(ApiCall.run_id == run_id))


def cmd_plan(args):
    """Print the exact get-sourced-properties arguments for the next page of a run, or STOP."""
    prof = RUN_PROFILES[args.kind]
    params = _run_params(args.kind, args.page)
    cost = propertydata.CREDIT_COST["sourced-properties"](params)
    with Session() as s:
        used = _run_credits(s, args.run_id)
    ok = used + cost <= prof.max_credits and args.page * prof.results_per_page <= 500
    print(json.dumps({"run_id": args.run_id, "action": "CALL" if ok else "STOP", "mcp_arguments": params,
                      "estimated_credits": cost, "credits_used_this_run": used, "run_cap": prof.max_credits}, indent=1))


def cmd_import_response(args):
    """Wrap a sourced-properties response (saved MCP result) with its request, archive it and ingest it."""
    params = _run_params(args.kind, args.page)
    resp = json.loads(Path(args.file).read_text())
    resp = resp.get("response", resp)
    if resp.get("status") != "success":
        raise SystemExit(f"response not successful: {str(resp)[:300]}")
    if int(resp.get("radius", -1)) != params["radius"] or int(resp.get("page", -1)) != params["page"]:
        raise SystemExit(f"response radius/page {resp.get('radius')}/{resp.get('page')} do not match plan "
                         f"{params['radius']}/{params['page']} — refusing to import")
    now = datetime.now(timezone.utc)
    path = settings.raw_dir / f"{now:%Y%m%dT%H%M%SZ}_sourced-properties_{args.kind}_p{args.page}.json"
    path.write_text(json.dumps({"source": "propertydata", "endpoint": "sourced-properties", "fetched_via": "mcp",
                                "fetched_at": now.isoformat(), "run_id": args.run_id, "request": params,
                                "response": resp}, indent=1))
    res = import_raw_file(path)
    prof = RUN_PROFILES[args.kind]
    with Session() as s:
        used = _run_credits(s, args.run_id)
    nxt = args.page + 1
    more = res["page_full"] and used + prof.results_per_page // 10 <= prof.max_credits and \
        nxt * prof.results_per_page <= 500
    print(json.dumps({**res, "saved": path.name, "credits_used_this_run": used,
                      "next": f"page {nxt}" if more else "DONE",
                      "complete": not res["page_full"]}, indent=1))


def cmd_finish(args):
    """After a complete weekly sweep, mark in-scope active properties that were not seen as REMOVED."""
    from .comps import haversine_miles
    prof = RUN_PROFILES[args.kind]
    lat, lng = map(float, settings.search.location.split(","))
    with Session.begin() as s:
        raws = [r.id for r in s.scalars(select(RawResponse).join(ApiCall, ApiCall.raw_response_id == RawResponse.id)
                                        .where(ApiCall.run_id == args.run_id))]
        if not raws:
            raise SystemExit(f"no responses recorded for run {args.run_id}")
        last = s.scalar(select(RawResponse).where(RawResponse.id == max(raws)))
        complete = len(last.payload.get("properties", [])) < prof.results_per_page
        seen = set(s.scalars(select(ListingObservation.property_id).where(ListingObservation.raw_response_id.in_(raws))))
        removed = 0
        if args.kind == "weekly" and complete:
            for p in s.scalars(select(Property).where(Property.current_status.in_(("ACTIVE", "SSTC")))):
                if p.id in seen or p.latitude is None:
                    continue
                if haversine_miles(lat, lng, p.latitude, p.longitude) <= prof.radius_miles - 0.1 and \
                        (p.current_asking_price or 0) <= settings.search.max_price:
                    p.current_status = "REMOVED"
                    s.add(PropertyEvent(property_id=p.id, event_type="REMOVED",
                                        event_data={"reason": "absent from complete weekly sweep",
                                                    "last_seen": p.last_seen.isoformat(), "run_id": args.run_id}))
                    removed += 1
    print(json.dumps({"run_id": args.run_id, "complete": complete, "seen": len(seen), "marked_removed": removed}))


def portal_link(source_ref: str | None) -> str | None:
    """Direct portal URL from a PropertyData id (R/Z/O prefix + portal listing id)."""
    if not source_ref:
        return None
    pid = source_ref[1:]
    return {"R": f"https://www.rightmove.co.uk/properties/{pid}",
            "Z": f"https://www.zoopla.co.uk/for-sale/details/{pid}/",
            "O": f"https://www.onthemarket.com/details/{pid}/"}.get(source_ref[:1])


MANUAL_STATUSES = ("SSTC", "SOLD", "WITHDRAWN", "ACTIVE")


def cmd_mark(args):
    """Record what a person saw on the portal. ACTIVE clears a previous mark."""
    with Session.begin() as s:
        for pid in args.ids:
            p = s.get(Property, pid)
            if p is None:
                raise SystemExit(f"no property {pid}")
            p.manual_status = None if args.status == "ACTIVE" else args.status
            p.manual_status_date = datetime.now(timezone.utc)
            if p.current_status not in ("EXCLUDED", "REMOVED"):
                p.current_status = args.status
            s.add(PropertyEvent(property_id=p.id, event_type=f"MANUAL_{args.status}",
                                event_data={"note": args.note} if args.note else None))
            print(f"{pid} {p.precise_address or p.address} -> {args.status}")


# ---------------------------------------------------------------- outputs

SHORTLIST_RECS = ("CALL AGENT", "INVESTIGATE", "WATCH")


def _shortlist_rows(s):
    rows = []
    for p in s.scalars(select(Property).where(Property.current_status == "ACTIVE")):
        a = _latest_analysis(s, p.id)
        if not a or a.recommendation not in SHORTLIST_RECS:
            continue
        o = _latest_obs(s, p.id)
        srcs = list(s.scalars(select(ListingSource).where(ListingSource.property_id == p.id)
                              .order_by(ListingSource.last_seen.desc())))
        portals = [x.portal for x in srcs]
        rows.append(dict(
            recommendation=a.recommendation, score=round(a.opportunity_score, 1),
            address=p.precise_address or p.address, postcode=p.postcode, type=p.property_type, beds=p.bedrooms,
            sqft=p.floor_area_sqft, asking=p.current_asking_price, expected_price=a.expected_purchase_price,
            gdv_low=a.gdv_low, gdv_base=a.gdv_base, gdv_high=a.gdv_high, confidence=a.confidence,
            max_offer_for_40k=a.maximum_purchase_price_for_target, opening_offer=a.opening_offer,
            offer_gap=a.offer_gap, profit_at_expected_price=a.expected_profit_at_asking,
            refurb=a.refurb_cost, contingency=a.contingency, sdlt=a.sdlt, buy_legal_survey=a.legal_cost,
            holding=a.holding_cost, finance=a.finance_cost,
            sale_agent_legal=a.selling_cost,
            auction_uplift_or_fee=(a.expected_purchase_price or a.asking_price) - a.asking_price,
            total_costs=notes.total_costs(a), fees_summary=notes.fees_summary(a),
            notes=notes.headline(p, o, a, portals), risks="|".join(p.risk_flags or []),
            days_on_market=o.days_on_market if o else None, first_seen=f"{p.first_seen:%Y-%m-%d}",
            id=p.id, check_on_portal=" ".join(filter(None, (portal_link(x.source_ref) for x in srcs))),
            url=p.portal_url))
    order = {r: i for i, r in enumerate(SHORTLIST_RECS)}
    return sorted(rows, key=lambda r: (order[r["recommendation"]], -r["score"]))


def cmd_shortlist(args):
    with Session() as s:
        rows = _shortlist_rows(s)
    out = settings.output_dir / "shortlist.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows £ correctly
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} shortlisted -> {out}")


def _deal_sheet(s, p, a) -> str:
    o = _latest_obs(s, p.id)
    srcs = list(s.scalars(select(ListingSource).where(ListingSource.property_id == p.id)))
    links = [x for x in (portal_link(x.source_ref) for x in srcs) if x] or [p.portal_url]
    comps = list(s.scalars(select(Comparable).where(Comparable.analysis_id == a.id)
                           .order_by(Comparable.similarity_score.desc())))
    ex = a.assumptions or {}
    sqft = ex.get("subject_sqft")
    fl = settings.flip
    lines = [f"# {p.precise_address or p.address}", "",
             f"**{a.recommendation}** · confidence {a.confidence} · appraised {a.analysis_date:%Y-%m-%d}", "",
             f"- {p.property_type or '?'}, {p.bedrooms or '?'} bed, "
             + (f"{sqft} sq ft ({ex.get('subject_sqft_source')})" if sqft else "floor area unknown"),
             f"- Asking £{a.asking_price:,}" + (f" · expected purchase £{a.expected_purchase_price:,}"
                                                 if a.expected_purchase_price != a.asking_price else ""),
             f"- Listing: {notes.headline(p, o, None, [x.portal for x in srcs])}",
             *[f"- Portal: {u}" for u in links], "",
             "## Comparable sales (HM Land Registry"
             + (" + EPC floor areas)" if any(c.price_per_sqft for c in comps) else ")"), "",
             "| Address | Sold | Price | Sq ft | £/sq ft | Miles |", "|---|---|---:|---:|---:|---:|"]
    for c in comps[:15]:
        lines.append(f"| {c.comparable_address.title()} | {c.sale_date:%b %Y} | £{c.sale_price:,} | "
                     f"{c.floor_area_sqft or '–'} | {f'£{c.price_per_sqft:.0f}' if c.price_per_sqft else '–'} | "
                     f"{c.distance_miles:.2f} |")
    if len(comps) > 15:
        lines.append(f"| … {len(comps) - 15} more | | | | | |")
    psf = (lambda v: f" (£{v / sqft:.0f}/sq ft)") if sqft else (lambda v: "")
    lines += ["", f"Basis: {a.gdv_source}", "", "## After-works value", "",
              f"- Low £{a.gdv_low:,}{psf(a.gdv_low)}", f"- **Base £{a.gdv_base:,}**{psf(a.gdv_base)}",
              f"- High £{a.gdv_high:,}{psf(a.gdv_high)}", "",
              f"## Costs at £{a.expected_purchase_price:,}", "", "| Item | £ |", "|---|---:|",
              f"| Refurb ({ex.get('refurb_basis', 'default')}) | {a.refurb_cost:,} |",
              f"| Contingency ({fl.contingency_pct_of_refurb:.0%} of refurb) | {a.contingency:,} |",
              f"| SDLT (incl. {fl.sdlt_surcharge:.0%} surcharge) | {a.sdlt:,} |",
              f"| Buy legal + survey | {a.legal_cost:,} |",
              f"| Holding ({fl.months_held} months) | {a.holding_cost:,} |",
              f"| Sale agent + legal | {a.selling_cost:,} |"]
    if a.finance_cost:
        lines.append(f"| Finance | {a.finance_cost:,} |")
    if a.expected_purchase_price != a.asking_price:
        lines.append(f"| Auction uplift / fee | {a.expected_purchase_price - a.asking_price:,} |")
    lines += [f"| **Total costs** | **{notes.total_costs(a) - (a.expected_purchase_price - a.asking_price):,}** |", ""]
    prof = {k: finance.deal(a.expected_purchase_price, g, a.refurb_cost).net_profit
            for k, g in (("low", a.gdv_low), ("base", a.gdv_base), ("high", a.gdv_high))}
    mpp = {k: finance.max_purchase_price(g, a.refurb_cost)
           for k, g in (("low", a.gdv_low), ("base", a.gdv_base), ("high", a.gdv_high))}
    lines += ["## Result", "",
              "| | Low | Base | High |", "|---|---:|---:|---:|",
              f"| Profit at £{a.expected_purchase_price:,} | £{prof['low']:,} | **£{prof['base']:,}** | £{prof['high']:,} |",
              f"| Max offer for £{fl.target_profit // 1000}k profit | £{mpp['low']:,} | **£{mpp['base']:,}** | £{mpp['high']:,} |",
              "", f"Opening offer £{a.opening_offer:,} · gap to asking £{a.offer_gap:,}",
              "", "Risks: " + (", ".join(p.risk_flags) if p.risk_flags else "none flagged from listing text"),
              "", "_Contains HM Land Registry data © Crown copyright and database right 2026 and EPC data "
              "© Crown copyright, both licensed under the Open Government Licence v3.0._"]
    return "\n".join(lines) + "\n"


def cmd_deal_sheets(args):
    """One markdown deal sheet per shortlisted property: output/deals/<id>-<street>.md (free)."""
    import re
    out = settings.output_dir / "deals"
    out.mkdir(parents=True, exist_ok=True)
    with Session() as s:
        rows = _shortlist_rows(s)
        for r in rows:
            p, a = s.get(Property, r["id"]), _latest_analysis(s, r["id"])
            slug = re.sub(r"[^a-z0-9]+", "-", (p.precise_address or p.address).split(",")[0].lower()).strip("-")
            (out / f"{p.id:03d}-{slug}.md").write_text(_deal_sheet(s, p, a))
    print(f"{len(rows)} deal sheets -> {out}")


EVENT_LABELS = [
    ("ENTERED_BUY_RANGE", "Newly in viable offer range"), ("PRICE_REDUCTION", "Price reductions"),
    ("FIRST_SEEN", "New opportunities"), ("BACK_ON_MARKET", "Back on market"), ("RELISTED", "Relisted"),
    ("DAYS_120", "Crossed 120 days"), ("DAYS_90", "Crossed 90 days"), ("DAYS_60", "Crossed 60 days"),
    ("SCORE_UPGRADE", "Biggest score improvements"), ("LEFT_BUY_RANGE", "Left viable range"),
    ("PRICE_INCREASE", "Price increases"), ("REMOVED", "Removed / sold"),
    ("PRICE_CHANGE_AFTER_MANUAL_STATUS", "Re-priced after you marked them sold/STC (sale may have fallen through)"),
]


def cmd_digest(args):
    """Changes-only report: events from the last N hours plus the current top shortlist."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    with Session() as s:
        evs = [e for e in s.scalars(select(PropertyEvent).order_by(PropertyEvent.id))
               if (e.event_date.replace(tzinfo=e.event_date.tzinfo or timezone.utc) >= cutoff)
               and not (e.event_data or {}).get("backfilled")
               and not (e.event_data or {}).get("initial")]  # thresholds already passed when first seen
        by_type = defaultdict(list)
        for e in evs:
            by_type[e.event_type].append(e)
        short = _shortlist_rows(s)
        recs = {r: sum(1 for x in short if x["recommendation"] == r) for r in SHORTLIST_RECS}
        rejected = sum(1 for p in s.scalars(select(Property).where(Property.current_status == "ACTIVE"))
                       if (a := _latest_analysis(s, p.id)) and a.recommendation == "REJECT")
        total_credits = s.scalar(select(func.coalesce(func.sum(ApiCall.credits_reported), 0)))
        run_credits = s.scalar(select(func.coalesce(func.sum(ApiCall.credits_reported), 0))
                               .where(ApiCall.called_at >= cutoff.replace(tzinfo=None)))
        lines = [f"# Flip Watch — {datetime.now(timezone.utc):%a %d %b %Y}", "",
                 f"CALL AGENT {recs['CALL AGENT']} · INVESTIGATE {recs['INVESTIGATE']} · WATCH {recs['WATCH']} · "
                 f"REJECT {rejected} · credits this period {run_credits} · total used {total_credits}"
                 f" of {settings.credit_allowance}", ""]
        for etype, label in EVENT_LABELS:
            items = by_type.get(etype, [])
            if not items:
                continue
            lines.append(f"## {label} ({len(items)})")
            for e in items[:args.limit]:
                p = s.get(Property, e.property_id)
                a = _latest_analysis(s, p.id)
                d = e.event_data or {}
                change = f"£{d['from']:,}→£{d['to']:,}" if "from" in d and "to" in d else ""
                verdict = f"{a.recommendation} (gap £{a.offer_gap:,})" if a else "not appraised"
                lines.append(f"- {p.precise_address or p.address} — £{p.current_asking_price:,} {change} · {verdict}")
            if len(items) > args.limit:
                lines.append(f"- … and {len(items) - args.limit} more")
            lines.append("")
        lines.append("## Top opportunities")
        for r in short[:args.limit]:
            lines.append(f"- **{r['recommendation']}** {r['score']} · {r['address']} · ask £{r['asking']:,} · "
                         f"max £{r['max_offer_for_40k']:,} · gap £{r['offer_gap']:,} — {r['notes'][:160]}")
    out = settings.output_dir / "daily_report.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main():
    init_db()
    ap = argparse.ArgumentParser(prog="flipwatch")
    sub = ap.add_subparsers(required=True)
    d = sub.add_parser("discover")
    d.add_argument("--lists")
    d.add_argument("--results", type=int, default=10)
    d.add_argument("--page", type=int, default=1)
    d.add_argument("--radius", type=int)
    d.add_argument("--max-price", type=int)
    d.add_argument("--confirm", action="store_true")
    d.set_defaults(fn=cmd_discover)
    i = sub.add_parser("import")
    i.add_argument("files", nargs="+")
    i.set_defaults(fn=cmd_import)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    sub.add_parser("credits").set_defaults(fn=cmd_credits)
    ls = sub.add_parser("load-sold-prices")
    ls.add_argument("years", type=int, nargs="+")
    ls.set_defaults(fn=cmd_load_sold)
    sc = sub.add_parser("screen")
    sc.add_argument("ids", type=int, nargs="*")
    sc.add_argument("--changed", action="store_true", help="only properties new or re-priced since last appraisal")
    sc.set_defaults(fn=cmd_screen)
    pl = sub.add_parser("plan")
    pl.add_argument("kind", choices=list(RUN_PROFILES))
    pl.add_argument("--run-id", required=True)
    pl.add_argument("--page", type=int, default=1)
    pl.set_defaults(fn=cmd_plan)
    ir = sub.add_parser("import-response")
    ir.add_argument("file")
    ir.add_argument("--kind", choices=list(RUN_PROFILES), required=True)
    ir.add_argument("--run-id", required=True)
    ir.add_argument("--page", type=int, default=1)
    ir.set_defaults(fn=cmd_import_response)
    fi = sub.add_parser("finish-run")
    fi.add_argument("kind", choices=list(RUN_PROFILES))
    fi.add_argument("--run-id", required=True)
    fi.set_defaults(fn=cmd_finish)
    sub.add_parser("shortlist").set_defaults(fn=cmd_shortlist)
    sub.add_parser("deal-sheets").set_defaults(fn=cmd_deal_sheets)
    ee = sub.add_parser("enrich-epc", help="EPC floor areas for comps around the shortlist (free)")
    ee.add_argument("ids", type=int, nargs="*")
    ee.set_defaults(fn=cmd_enrich_epc)
    mk = sub.add_parser("mark", help="record a status seen on the portal (overrides the feed)")
    mk.add_argument("ids", type=int, nargs="+")
    mk.add_argument("--status", choices=MANUAL_STATUSES, required=True)
    mk.add_argument("--note")
    mk.set_defaults(fn=cmd_mark)
    dg = sub.add_parser("digest")
    dg.add_argument("--hours", type=int, default=26)
    dg.add_argument("--limit", type=int, default=10)
    dg.set_defaults(fn=cmd_digest)
    a = sub.add_parser("analyse")
    a.add_argument("property_id", type=int)
    a.add_argument("--gdv", type=int, required=True)
    a.add_argument("--gdv-low", type=int)
    a.add_argument("--gdv-high", type=int)
    a.add_argument("--gdv-source", default="manual")
    a.add_argument("--refurb", type=int)
    a.add_argument("--asking", type=int)
    a.add_argument("--confidence", choices=["HIGH", "MEDIUM", "LOW"], default="LOW")
    a.set_defaults(fn=cmd_analyse)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
