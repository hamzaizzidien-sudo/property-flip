"""Command line entry point:  python -m flipwatch.cli <command> ..."""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import func, select

from . import finance, scoring
from .config import settings
from .db import Analysis, ApiCall, ListingObservation, Property, PropertyEvent, Session, init_db
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
            max_purchase=a.maximum_purchase_price_for_target if a else None,
            offer_gap=a.offer_gap if a else None, recommendation=a.recommendation if a else None,
            url=p.portal_url))
    return sorted(rows, key=lambda r: -r["screening_score"])


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
    """Record a deal appraisal with GDV supplied from comparables (this does not fetch comps yet)."""
    with Session.begin() as s:
        p = s.get(Property, args.property_id)
        asking = args.asking or p.current_asking_price
        r = finance.appraise(asking, args.gdv_low or args.gdv, args.gdv, args.gdv_high or args.gdv,
                             args.refurb, args.confidence, p.risk_flags or [])
        o = _latest_obs(s, p.id)
        comp = scoring.screening_components(o.days_on_market, o.price_history or [], asking,
                                            p.motivation_flags or [], p.risk_flags or [])
        score = (sum(comp.values()) + scoring.financial_points(r["offer_gap"], asking)
                 + scoring.CONFIDENCE_POINTS[args.confidence])
        d = r["at_asking"]
        prev = s.scalar(select(Analysis).where(Analysis.property_id == p.id).order_by(Analysis.analysis_date.desc()))
        a = Analysis(property_id=p.id, asking_price=asking, gdv_low=args.gdv_low or args.gdv, gdv_base=args.gdv,
                     gdv_high=args.gdv_high or args.gdv, gdv_source=args.gdv_source, refurb_cost=d["refurb"],
                     sdlt=d["sdlt"], legal_cost=d["legal"], finance_cost=d["finance"], holding_cost=d["holding"],
                     selling_cost=d["selling"], contingency=d["contingency"],
                     expected_profit_at_asking=d["net_profit"],
                     maximum_purchase_price_for_target=r["max_purchase_price"], offer_gap=r["offer_gap"],
                     opening_offer=r["opening_offer"], opportunity_score=score,
                     recommendation=r["recommendation"], confidence=args.confidence, risks=p.risk_flags,
                     assumptions=vars(settings.flip))
        s.add(a)
        in_range = asking <= r["max_purchase_price"]
        was_in_range = prev is not None and prev.asking_price <= prev.maximum_purchase_price_for_target
        if in_range and not was_in_range:
            s.add(PropertyEvent(property_id=p.id, event_type="ENTERED_BUY_RANGE", event_data={"asking": asking}))
        if was_in_range and not in_range:
            s.add(PropertyEvent(property_id=p.id, event_type="LEFT_BUY_RANGE", event_data={"asking": asking}))
        if prev and prev.opportunity_score is not None and score - prev.opportunity_score >= 10:
            s.add(PropertyEvent(property_id=p.id, event_type="SCORE_UPGRADE",
                                event_data={"from": prev.opportunity_score, "to": score}))
    print(json.dumps({**r, "opportunity_score": score}, indent=1))


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
