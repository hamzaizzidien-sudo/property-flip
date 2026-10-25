"""100-point opportunity score. Financial spread (40) + GDV confidence (10) + strategic (5) need an
analysis; the remaining 45 points are 'screening' signals available for free at discovery time."""

RISK_PENALTY = {
    "non_standard_construction": 15, "multi_unit_or_block": 15, "shared_ownership": 15, "land_only": 15,
    "tenanted": 6, "leasehold": 4, "short_lease": 10, "flood": 5, "subsidence": 8, "japanese_knotweed": 6,
    "auction_guide_price": 2, "gdv_bedroom_mismatch": 10,  # comps likely larger -> GDV overstated
}
MOTIVATION_POINTS = {
    "repossessed": 5, "probate": 4, "no_chain": 3, "vacant": 3, "cash_buyers_only": 3, "quick_sale": 3,
    "back_on_market": 3, "auction": 2, "modern_method_of_auction": 1,
}


def total_reduction_pct(price_history: list[dict], current: int | None) -> float:
    if not price_history or not current:
        return 0.0
    peak = max(h["price"] for h in price_history if h["price"])
    return max(0.0, (peak - current) / peak * 100)


def screening_components(days_on_market, price_history, current_price, motivation, risks) -> dict:
    dom = days_on_market or 0
    tom = 15 if dom >= 120 else 12 if dom >= 90 else 8 if dom >= 60 else 4 if dom >= 30 else 0
    red = total_reduction_pct(price_history, current_price)
    n_red = sum(1 for a, b in zip(price_history, price_history[1:]) if b["price"] < a["price"])
    reductions = min(10, (10 if red >= 15 else 7 if red >= 10 else 4 if red >= 5 else 2 if red > 0 else 0)
                     + (2 if n_red >= 3 else 0))
    condition = 10 if "derelict_poor_condition" in motivation else 7 if "needs_modernisation" in motivation else 0
    condition = min(10, condition + (3 if "poor_epc" in motivation else 0))
    motiv = min(10, sum(MOTIVATION_POINTS.get(m, 0) for m in motivation))
    penalty = -min(15, sum(RISK_PENALTY.get(r, 0) for r in risks))
    return dict(time_on_market=tom, price_reductions=reductions, condition=condition,
                seller_motivation=motiv, risk_penalty=penalty)


def financial_points(offer_gap: int, asking: int) -> float:
    """40 points when asking <= max purchase price, falling to 0 at a gap of 25% of asking."""
    if asking <= 0:
        return 0.0
    gap_pct = max(0.0, offer_gap / asking)
    return round(max(0.0, 40 * (1 - gap_pct / 0.25)), 1)


CONFIDENCE_POINTS = {"HIGH": 10, "MEDIUM": 6, "LOW": 2}
