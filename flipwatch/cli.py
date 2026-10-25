"""Command line entry point:  python -m flipwatch.cli <command> ..."""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import func, select

from . import comps as comps_mod
from . import finance, scoring
from .config import settings
from .db import (Analysis, ApiCall, Comparable, ListingObservation, Property, PropertyEvent, Session,
                 init_db)
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


def record_analysis(s, p, asking, gdv_low, gdv, gdv_high, refurb, confidence, gdv_source):
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
                 assumptions=vars(settings.flip))
    s.add(a)
    s.flush()
    in_range = r["offer_gap"] <= 0
    was_in_range = prev is not None and prev.offer_gap is not None and prev.offer_gap <= 0
    if in_range and not was_in_range:
        s.add(PropertyEvent(property_id=p.id, event_type="ENTERED_BUY_RANGE", event_data={"asking": asking}))
    if was_in_range and not in_range:
        s.add(PropertyEvent(property_id=p.id, event_type="LEFT_BUY_RANGE", event_data={"asking": asking}))
    if prev and prev.opportunity_score is not None and score - prev.opportunity_score >= 10:
        s.add(PropertyEvent(property_id=p.id, event_type="SCORE_UPGRADE",
                            event_data={"from": prev.opportunity_score, "to": score}))
    return r, score, a


def cmd_screen(args):
    """Value every active candidate from local Land Registry comps and record an appraisal (free)."""
    with Session.begin() as s:
        props = [p for p in s.scalars(select(Property).where(Property.current_status == "ACTIVE"))
                 if not args.ids or p.id in args.ids]
        for p in props:
            beds = p.bedrooms or 3
            if (p.property_type in ("semi-detached", "detached") and beds <= 2) or \
                    (p.property_type == "terraced" and (beds >= 4 or beds <= 1)):
                p.risk_flags = sorted(set(p.risk_flags or []) | {"gdv_bedroom_mismatch"})
            comps = comps_mod.find_comps(s, p)
            g = comps_mod.gdv_from_comps(comps)
            if not g:
                print(f"{p.id:>3} {p.address[:40]:<40} too few comps ({len(comps)})")
                continue
            r, score, a = record_analysis(s, p, p.current_asking_price, g["gdv_low"], g["gdv_base"],
                                          g["gdv_high"], None, g["confidence"],
                                          f"LR comps v0: n={g['n']} street={g['same_street']} spread={g['spread']}")
            for c in g["kept"]:
                s.add(Comparable(subject_property_id=p.id, analysis_id=a.id, comparable_address=c["address"],
                                 sale_price=c["price"], sale_date=c["sale_date"], property_type=c["property_type"],
                                 distance_miles=c["distance"], similarity_score=round(c["weight"], 3),
                                 source="land_registry"))
            print(f"{p.id:>3} {p.address[:40]:<40} ask £{p.current_asking_price:>7,} "
                  f"exp £{r['expected_purchase_price']:>7,} GDV £{g['gdv_base']:>7,} "
                  f"(n={g['n']}) max £{r['max_purchase_price']:>7,} gap £{r['offer_gap']:>8,} {r['recommendation']}")


def cmd_load_sold(args):
    """Load HM Land Registry Price Paid yearly files into sold_prices (free)."""
    from .sources.landregistry import load_year
    for y in args.years:
        print(y, load_year(y), flush=True)


def cmd_credits(args):
    with Session() as s:
        for c in s.scalars(select(ApiCall).order_by(ApiCall.called_at)):
            print(c.called_at, c.endpoint, c.via, c.credits_reported, c.result_count)


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
    sc.set_defaults(fn=cmd_screen)
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
