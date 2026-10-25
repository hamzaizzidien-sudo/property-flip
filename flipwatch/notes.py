"""One-line headline notes for the shortlist, built from listing data and agent text (free)."""
import re


def _k(n: int) -> str:
    return f"£{n / 1000:.0f}k" if n % 1000 == 0 else f"£{n / 1000:.1f}k"


def headline(prop, obs, analysis=None, portals: list[str] | None = None) -> str:
    text = prop.summary or ""
    low = text.lower()
    hist = (obs.price_history if obs else None) or []
    bits = []

    reductions = [(a, b) for a, b in zip(hist, hist[1:]) if b["price"] < a["price"]]
    if reductions:
        peak = max(h["price"] for h in hist)
        pct = (peak - prop.current_asking_price) / peak * 100
        bits.append(f"Reduced {len(reductions)}x {_k(peak)}→{_k(prop.current_asking_price)} (−{pct:.0f}%), "
                    f"last cut {hist[-1]['date']}")
    if obs and obs.days_on_market:
        bits.append(f"{obs.days_on_market} days on market")
    if m := re.search(r"off the market for (\d+) days", low):
        bits.append(f"back on market after {m.group(1)} days off")
    elif "back-on-market" in (prop.current_lists or []):
        bits.append("back on market")

    if m := re.search(r"auction on (?:the )?(\d{1,2})(?:st|nd|rd|th)? (\w+)(?: (\d{4}))?", low):
        bits.append(f"auction {m.group(1)} {m.group(2).title()}{(' ' + m.group(3)) if m.group(3) else ''}")
    elif "modern method of auction" in low:
        bits.append("Modern Method of Auction (buyer reservation fee)")
    elif "auction" in low:
        bits.append("auction (guide price)")
    if m := re.search(r"highest offer[^£]*£([\d,]+)", low):
        bits.append(f"highest offer so far £{m.group(1)}")

    flags = [
        ("repossess", "repossessed"), ("probate", "probate"), ("cash buyers only", "cash buyers only"),
        ("vacant possession", "vacant"), ("no onward chain", "no chain"), ("chain free", "no chain"),
        ("open to negotiation", "vendor open to negotiation"), ("full renovation", "full renovation needed"),
        ("full refurbishment", "full refurbishment needed"), ("requires refurbishment", "needs refurbishment"),
        ("unmodernised", "unmodernised"), ("modernisation", "needs modernising"),
        ("tenants in situ", "TENANTED"), ("tenanted", "TENANTED"),
        ("non standard construction", "NON-STANDARD CONSTRUCTION"), ("knotweed", "JAPANESE KNOTWEED"),
        ("leasehold", "leasehold"), ("freehold", "freehold"),
    ]
    seen = set()
    for needle, label in flags:
        if needle in low and label not in seen:
            seen.add(label)
            bits.append(label)
    if m := re.search(r"epc (?:rating )?(?:is |of )?([a-g])\b", low):
        bits.append(f"EPC {m.group(1).upper()}")
    if prop.floor_area_sqft:
        bits.append(f"{prop.floor_area_sqft} sq ft")
    if "gdv_bedroom_mismatch" in (prop.risk_flags or []):
        bits.append("CHECK GDV: unusual beds for type, comps may be larger")
    if portals and len(set(portals)) > 1:
        bits.append("listed on " + "+".join(sorted(set(portals))))
    if analysis and analysis.gdv_source:
        bits.append(analysis.gdv_source.replace("LR comps v0: ", "comps "))
    return "; ".join(bits)


def fees_summary(a) -> str:
    """Itemised cost stack at the expected purchase price."""
    extra = (a.expected_purchase_price or a.asking_price) - a.asking_price
    parts = [f"Refurb £{a.refurb_cost:,}", f"Contingency £{a.contingency:,}", f"SDLT £{a.sdlt:,}",
             f"Buy legal+survey £{a.legal_cost:,}", f"Holding £{a.holding_cost:,}",
             f"Sale agent+legal £{a.selling_cost:,}"]
    if a.finance_cost:
        parts.append(f"Finance £{a.finance_cost:,}")
    if extra:
        parts.append(f"Auction uplift/fee £{extra:,}")
    return "; ".join(parts) + f" = £{total_costs(a):,}"


def total_costs(a) -> int:
    extra = (a.expected_purchase_price or a.asking_price) - a.asking_price
    return (a.refurb_cost + a.contingency + a.sdlt + a.legal_cost + a.holding_cost + a.selling_cost
            + (a.finance_cost or 0) + extra)
