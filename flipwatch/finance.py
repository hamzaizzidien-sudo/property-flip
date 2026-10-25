"""Flip economics, computed locally (no API credits): SDLT, cost stack, max purchase price, offer gap."""
from dataclasses import asdict, dataclass

from .config import FlipAssumptions, settings

# England & NI residential SDLT bands from 1 April 2025 (standard rates).
SDLT_BANDS = [(125_000, 0.0), (250_000, 0.02), (925_000, 0.05), (1_500_000, 0.10), (float("inf"), 0.12)]


def sdlt(price: float, surcharge: float = 0.05) -> int:
    """SDLT for one residential purchase. `surcharge` is the higher-rates uplift for additional
    dwellings (5% since 31 Oct 2024); pass 0 for a buyer's only home. Purchases under £40k pay no
    higher-rates SDLT. (Companies buying >£500k face 17% flat — irrelevant at this price range.)"""
    if surcharge and price < 40_000:
        return 0
    tax, lower = 0.0, 0
    for upper, rate in SDLT_BANDS:
        if price > lower:
            tax += (min(price, upper) - lower) * (rate + surcharge)
        lower = upper
    return int(tax)


@dataclass
class DealCosts:
    purchase_price: int
    gdv: int
    refurb: int
    sdlt: int
    legal: int  # buy legal + survey
    finance: int
    holding: int
    selling: int  # agent + sale legal
    contingency: int
    net_profit: int


def deal(purchase_price: float, gdv: float, refurb: float | None = None,
         a: FlipAssumptions | None = None) -> DealCosts:
    a = a or settings.flip
    refurb = a.default_refurb if refurb is None else refurb
    borrowed = purchase_price * a.finance_ltv
    finance = borrowed * (a.finance_rate_monthly * a.months_held + a.finance_arrangement_pct)
    costs = dict(
        sdlt=sdlt(purchase_price, a.sdlt_surcharge),
        legal=a.buy_legal + a.survey,
        finance=round(finance),
        holding=a.holding_per_month * a.months_held,
        selling=round(gdv * a.agent_fee_pct + a.sale_legal),
        contingency=round(refurb * a.contingency_pct_of_refurb),
    )
    profit = gdv - purchase_price - refurb - sum(costs.values())
    return DealCosts(purchase_price=round(purchase_price), gdv=round(gdv), refurb=round(refurb),
                     net_profit=round(profit), **costs)


def max_purchase_price(gdv: float, refurb: float | None = None, target_profit: float | None = None,
                       a: FlipAssumptions | None = None) -> int:
    """Highest price that still leaves `target_profit`. Profit falls monotonically with price
    (SDLT and finance both rise with it), so bisection solves the SDLT circularity exactly."""
    a = a or settings.flip
    target = a.target_profit if target_profit is None else target_profit
    lo, hi = 0.0, float(gdv)
    if deal(lo, gdv, refurb, a).net_profit < target:
        return 0
    for _ in range(60):
        mid = (lo + hi) / 2
        if deal(mid, gdv, refurb, a).net_profit >= target:
            lo = mid
        else:
            hi = mid
    return int(lo // 500 * 500)  # round down to £500


def required_gdv(purchase_price: float, refurb: float | None = None, target_profit: float | None = None,
                 a: FlipAssumptions | None = None) -> int:
    """GDV needed to hit the target profit when buying at `purchase_price` — a zero-comps sanity check."""
    a = a or settings.flip
    target = a.target_profit if target_profit is None else target_profit
    lo, hi = float(purchase_price), float(purchase_price) * 3 + 200_000
    for _ in range(60):
        mid = (lo + hi) / 2
        if deal(purchase_price, mid, refurb, a).net_profit >= target:
            hi = mid
        else:
            lo = mid
    return int(-(-hi // 1000) * 1000)  # round up to £1k


def opening_offer(max_price: int, a: FlipAssumptions | None = None) -> int:
    a = a or settings.flip
    return int(max_price * (1 - a.opening_offer_discount) // 1000 * 1000)


def recommend(asking: int, max_price: int, confidence: str, risks: list[str]) -> str:
    """Offer-gap bands from the brief: <=0 CALL AGENT, <=15k INVESTIGATE, <=35k WATCH, else REJECT."""
    hard = {"non_standard_construction", "multi_unit_or_block", "shared_ownership", "land_only"}
    if max_price <= 0 or hard & set(risks):
        return "REJECT" if max_price <= 0 or asking - max_price > 15_000 else "INVESTIGATE"
    gap = asking - max_price
    if gap <= 0:
        return "CALL AGENT" if confidence in ("HIGH", "MEDIUM") else "INVESTIGATE"
    if gap <= 15_000:
        return "INVESTIGATE"
    if gap <= 35_000:
        return "WATCH"
    return "REJECT"


def appraise(asking: int, gdv_low: int, gdv_base: int, gdv_high: int, refurb: int | None,
             confidence: str, risks: list[str], a: FlipAssumptions | None = None) -> dict:
    a = a or settings.flip
    at_ask = deal(asking, gdv_base, refurb, a)
    mpp = max_purchase_price(gdv_base, refurb, a=a)
    return dict(
        at_asking=asdict(at_ask),
        max_purchase_price=mpp,
        max_purchase_price_gdv_low=max_purchase_price(gdv_low, refurb, a=a),
        max_purchase_price_gdv_high=max_purchase_price(gdv_high, refurb, a=a),
        offer_gap=asking - mpp,
        opening_offer=opening_offer(mpp, a),
        recommendation=recommend(asking, mpp, confidence, risks),
    )
