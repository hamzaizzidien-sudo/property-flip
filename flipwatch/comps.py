"""Comparable-sales GDV from the local Land Registry table (zero API credits).

v1 (gdv_per_sqft, used when the subject's floor area is known and enough comps carry EPC floor areas):
  same selection as v0, then keep sales within ±25% of the subject's floor area (±35% if thin),
  drop £/sqft outliers outside 1.5 x IQR, and value the subject at the weighted 50th / 70th / 85th
  percentile £/sqft x subject sqft. Confidence MEDIUM with >= 8 size-matched comps and a low-high
  spread <= 20%, else LOW; HIGH needs an independent valuation cross-check.

v0 (gdv_from_comps — fallback when floor areas are missing):
  1. Candidate sales in the subject's outcode and nearby outcodes, last 24 months (36 if thin),
     same property type, standard-price (PPD A) resales, not new build.
  2. Distance from postcode centroids; keep <= 0.5 mile, widening to 1 mile if fewer than 8.
  3. Weight by distance, recency and same-street bonus; drop outliers outside 1.5 x IQR.
  4. Refurbished resale value sits in the upper part of a mixed-condition sample, so
     GDV low/base/high = weighted 50th / 70th / 85th percentiles.
  5. Confidence fixed at LOW until floor-area (EPC) matching and an independent valuation
     cross-check exist. Same-street sales are weighted 5x.
"""
import math
import re
from datetime import date, timedelta

import requests
from sqlalchemy import select

from .db import Postcode, SoldPrice

POSTCODES_IO = "https://api.postcodes.io"


def haversine_miles(a_lat, a_lng, b_lat, b_lng):
    r = 3958.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def postcode_coords(s, postcodes: set[str]) -> dict[str, tuple[float, float]]:
    """Centroids for full postcodes, cached in the `postcodes` table; misses fetched in bulk."""
    have = {p.postcode: (p.latitude, p.longitude)
            for p in s.scalars(select(Postcode).where(Postcode.postcode.in_(postcodes)))}
    missing = sorted(postcodes - have.keys())
    for i in range(0, len(missing), 100):
        r = requests.post(f"{POSTCODES_IO}/postcodes", json={"postcodes": missing[i:i + 100]}, timeout=60)
        r.raise_for_status()
        for item in r.json()["result"]:
            res = item["result"] or {}
            lat, lng = res.get("latitude"), res.get("longitude")
            s.merge(Postcode(postcode=item["query"], latitude=lat, longitude=lng))
            have[item["query"]] = (lat, lng)
    s.flush()
    return {k: v for k, v in have.items() if v[0] is not None}


def nearby_outcodes(s, lat, lng, radius_miles=2.0) -> list[str]:
    r = requests.get(f"{POSTCODES_IO}/outcodes", params={"lat": lat, "lon": lng, "radius": 16000, "limit": 30},
                     timeout=30)
    r.raise_for_status()
    out = [o["outcode"] for o in r.json()["result"] or []
           if haversine_miles(lat, lng, o["latitude"], o["longitude"]) <= radius_miles + 2]
    return out


def _wpercentile(values, weights, q):
    pairs = sorted(zip(values, weights))
    total = sum(weights)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * total:
            return v
    return pairs[-1][0]


def _street_of(address: str) -> str:
    first = address.split(",")[0].upper()
    return re.sub(r"^\s*\d+[A-Z]?[\.,]?\s+", "", first).strip()


def find_comps(s, prop, today: date | None = None, enough=lambda comps: len(comps) >= 8) -> list[dict]:
    today = today or date.today()
    if prop.latitude is None or prop.property_type not in ("detached", "semi-detached", "terraced", "bungalow"):
        return []
    ptypes = ["semi-detached", "detached"] if prop.property_type == "bungalow" else [prop.property_type]
    outs = nearby_outcodes(s, prop.latitude, prop.longitude)
    street = _street_of(prop.precise_address or prop.address)
    for months, radius in ((24, 0.5), (24, 1.0), (36, 1.0)):
        since = today - timedelta(days=months * 30.5)
        sales = list(s.scalars(select(SoldPrice).where(
            SoldPrice.outcode.in_(outs), SoldPrice.property_type.in_(ptypes), SoldPrice.ppd_category == "A",
            SoldPrice.new_build.is_(False), SoldPrice.sale_date >= since)))
        coords = postcode_coords(s, {x.postcode for x in sales})
        comps = []
        for x in sales:
            if x.postcode not in coords:
                continue
            d = haversine_miles(prop.latitude, prop.longitude, *coords[x.postcode])
            if d > radius:
                continue
            age_m = (today - x.sale_date).days / 30.5
            same_street = bool(street) and (x.street or "").upper() == street
            w = (1 / (0.15 + d)) * (0.5 ** (age_m / 18)) * (5.0 if same_street else 1.0)
            comps.append(dict(address=" ".join(filter(None, [x.saon, x.paon, x.street, x.town])),
                              postcode=x.postcode, price=x.price, sale_date=x.sale_date,
                              property_type=x.property_type, tenure=x.tenure, distance=round(d, 3),
                              same_street=same_street, weight=w, floor_area_sqft=x.floor_area_sqft))
        if enough(comps):
            break
    return comps


def gdv_from_comps(comps: list[dict]) -> dict | None:
    if len(comps) < 3:
        return None
    prices = sorted(c["price"] for c in comps)
    q1, q3 = prices[len(prices) // 4], prices[(3 * len(prices)) // 4]
    lo_f, hi_f = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    kept = [c for c in comps if lo_f <= c["price"] <= hi_f]
    v, w = [c["price"] for c in kept], [c["weight"] for c in kept]
    low, base, high = (_wpercentile(v, w, q) for q in (0.5, 0.7, 0.85))
    spread = (high - low) / base if base else 1
    n = len(kept)
    # No bedroom/floor-area matching yet, so never better than LOW (blocks CALL AGENT on comps alone).
    confidence = "LOW"
    return dict(gdv_low=int(low), gdv_base=int(base), gdv_high=int(high), n=n,
                n_removed=len(comps) - n, spread=round(spread, 3), confidence=confidence,
                same_street=sum(c["same_street"] for c in kept), kept=kept)


SIZE_BANDS = (0.25, 0.35)


def size_matched(comps: list[dict], sqft: int, band: float) -> list[dict]:
    return [c for c in comps if c.get("floor_area_sqft") and abs(c["floor_area_sqft"] / sqft - 1) <= band]


def gdv_per_sqft(comps: list[dict], sqft: int, min_n: int = 5) -> dict | None:
    for band in SIZE_BANDS:
        sized = size_matched(comps, sqft, band)
        if len(sized) >= min_n:
            break
    else:
        return None
    for c in sized:
        c["ppsf"] = c["price"] / c["floor_area_sqft"]
    ppsf = sorted(c["ppsf"] for c in sized)
    q1, q3 = ppsf[len(ppsf) // 4], ppsf[(3 * len(ppsf)) // 4]
    lo_f, hi_f = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    kept = [c for c in sized if lo_f <= c["ppsf"] <= hi_f]
    v, w = [c["ppsf"] for c in kept], [c["weight"] for c in kept]
    low, base, high = (_wpercentile(v, w, q) * sqft for q in (0.5, 0.7, 0.85))
    spread = (high - low) / base
    n = len(kept)
    return dict(gdv_low=int(low // 1000 * 1000), gdv_base=int(base // 1000 * 1000),
                gdv_high=int(high // 1000 * 1000), n=n, n_removed=len(sized) - n, spread=round(spread, 3),
                confidence="MEDIUM" if n >= 8 and spread <= 0.20 else "LOW", size_band=band,
                ppsf_low=round(low / sqft), ppsf_base=round(base / sqft), ppsf_high=round(high / sqft),
                same_street=sum(c["same_street"] for c in kept), kept=kept)


def estimated_sqft(comps: list[dict]) -> int | None:
    """Median EPC floor area of the nearest same-type sales, for subjects with no floor area."""
    near = sorted((c for c in comps if c.get("floor_area_sqft")), key=lambda c: c["distance"])[:15]
    if len(near) < 5:
        return None
    return sorted(c["floor_area_sqft"] for c in near)[len(near) // 2]
