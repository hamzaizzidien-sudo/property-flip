"""Zero-credit signal extraction from listing text and list membership."""
import re

MOTIVATION_PATTERNS = {
    "no_chain": r"no (onward )?chain|chain[- ]free",
    "vacant": r"vacant possession|\bvacant\b|empty (home|property)",
    "probate": r"\bprobate\b|deceased|executor",
    "repossessed": r"repossess",
    "cash_buyers_only": r"cash buyers? only|cash purchasers? only",
    "needs_modernisation": r"modernis|moderniz|updating|in need of (some )?(improvement|refurb)|refurbishment",
    "derelict_poor_condition": r"derelict|uninhabitable|fire damage|structural (issues|movement)|in poor condition",
    "auction": r"\bauction\b",
    "modern_method_of_auction": r"modern method of auction|\bmmoa\b",
    "quick_sale": r"quick sale|motivated seller|must be sold|priced to sell",
    "poor_epc": r"epc (rating )?(is )?[efg]\b",
}

RISK_PATTERNS = {
    "non_standard_construction": r"non[- ]standard construction|concrete construction|\bprc\b|steel[- ]framed|timber[- ]framed|\bcornish\b|\bwimpey no[- ]fines\b",
    "tenanted": r"tenants? in situ|tenanted|currently (let|achieving)|sitting tenant",
    "multi_unit_or_block": r"freehold block|two self[- ]contained|apartments|flats? (above|below)|duplex",
    "leasehold": r"\bleasehold\b",
    "short_lease": r"short lease",
    "flood": r"flood",
    "japanese_knotweed": r"knotweed",
    "subsidence": r"subsidence|underpinn",
    "land_only": r"\bplot of land\b|\bbuilding plot\b|\bland for sale\b",
    "shared_ownership": r"shared ownership|retirement|over 55|age restricted",
}

LIST_TO_MOTIVATION = {
    "auction-properties": "auction", "unmodernised-properties": "needs_modernisation",
    "derelict-properties": "derelict_poor_condition", "repossessed-properties": "repossessed",
    "cash-buyers-only-properties": "cash_buyers_only", "properties-with-no-chain": "no_chain",
    "quick-sale-properties": "quick_sale", "back-on-market": "back_on_market",
    "reduced-properties": "price_reduced", "slow-to-sell-properties": "slow_to_sell",
}


def extract(summary: str | None, lists: list[str]) -> tuple[list[str], list[str]]:
    text = (summary or "").lower()
    motivation = {k for k, p in MOTIVATION_PATTERNS.items() if re.search(p, text)}
    motivation |= {LIST_TO_MOTIVATION[l] for l in lists if l in LIST_TO_MOTIVATION}
    risks = {k for k, p in RISK_PATTERNS.items() if re.search(p, text)}
    if "auction" in motivation:
        risks.add("auction_guide_price")  # guide price is not an expected sale price
    return sorted(motivation), sorted(risks)
