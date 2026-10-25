"""PropertyData source: REST client with a hard credit guard, plus response normalisation.

Behaviour verified live on 2026-10-25 (via the PropertyData MCP connector, 1 credit):
  * /sourced-properties costs 1 credit per 10 results; response reports `api_calls_cost`.
  * Up to 5 lists may be comma-separated in one call; each property carries its own `lists`.
  * Results are ordered by distance from the centre; `result_count` is the page size,
    NOT the total match count — there is no total, so pagination cost cannot be known upfront.
  * `page * results` must not exceed 500 -> at most 500 results (50 credits) per query.
  * `reduced_by` is a percentage string, not pounds; `months_on_market` only on some records.
  * `price_history` is included (first listing price onward) — free history from day one.
"""
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..config import settings
from .base import Listing

BASE_URL = "https://api.propertydata.co.uk"
SOURCE = "propertydata"

# Documented costs we rely on. Anything not here must be checked before use.
CREDIT_COST = {
    "sourced-properties": lambda p: math.ceil(int(p.get("results", 10)) / 10),
}


class CreditLimitExceeded(RuntimeError):
    pass


class PropertyDataClient:
    def __init__(self, api_key: str | None = None, max_credits: int | None = None):
        self.api_key = api_key or settings.propertydata_api_key
        if not self.api_key:
            raise RuntimeError("PROPERTYDATA_API_KEY not set (put it in .env, never in code)")
        self.max_credits = settings.max_credits_per_run if max_credits is None else max_credits
        self.credits_used = 0
        self.run_id = uuid.uuid4().hex[:12]

    def estimate(self, endpoint: str, params: dict) -> int:
        if endpoint not in CREDIT_COST:
            raise ValueError(f"No known credit cost for {endpoint}; verify in docs before calling")
        return CREDIT_COST[endpoint](params)

    def get(self, endpoint: str, params: dict) -> tuple[dict, int]:
        cost = self.estimate(endpoint, params)
        if self.credits_used + cost > self.max_credits:
            raise CreditLimitExceeded(
                f"{endpoint} would cost {cost}; {self.credits_used}/{self.max_credits} already used this run")
        r = requests.get(f"{BASE_URL}/{endpoint}", params={"key": self.api_key, **params}, timeout=60)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            raise RuntimeError(f"PropertyData error: {data.get('message') or data}")
        self.credits_used += int(data.get("api_calls_cost", cost))
        return data, cost


def save_raw(endpoint: str, params: dict, response: dict, via: str, tag: str = "") -> Path:
    """Write the payload to data/raw. `params` must never contain the key."""
    assert "key" not in params
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = settings.raw_dir / f"{ts}_{endpoint}{('_' + tag) if tag else ''}.json"
    path.write_text(json.dumps({
        "source": SOURCE, "endpoint": endpoint, "fetched_via": via,
        "fetched_at": datetime.now(timezone.utc).isoformat(), "request": params, "response": response,
    }, indent=1))
    return path


_TYPE_MAP = {
    "detached house": "detached", "semi-detached house": "semi-detached",
    "terraced house": "terraced", "flat": "flat",
}


def _normalise_type(raw: str | None, standardised: str | None, summary: str) -> str:
    s = (summary or "").lower()
    if "bungalow" in (raw or "").lower() or re.search(r"\bbungalow\b", s):
        return "bungalow"
    if (raw or "").lower() in ("end_terrace", "end of terrace", "terraced", "mid_terrace"):
        return "terraced"  # PropertyData standardises end_terrace as semi; keep the raw intent
    return _TYPE_MAP.get((standardised or "").lower(), "other")


def _int(v):
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalise(item: dict) -> Listing:
    ref = item["id"]
    portal = {"R": "rightmove", "Z": "zoopla"}.get(ref[:1])
    summary = item.get("summary") or ""
    return Listing(
        source=SOURCE, source_ref=ref, portal=portal, url=item.get("url"),
        address=item.get("address", ""), precise_address=item.get("precise_address"),
        postcode=item.get("postcode"), price=_int(item.get("price")),
        latitude=_float(item.get("lat")), longitude=_float(item.get("lng")),
        property_type=_normalise_type(item.get("type"), item.get("type_standardised"), summary),
        property_type_raw=f"{item.get('type')} | {item.get('type_standardised')}",
        bedrooms=_int(item.get("bedrooms")), floor_area_sqft=_int(item.get("sqf")),
        sstc=bool(item.get("sstc")) if item.get("sstc") is not None else None,
        days_on_market=_int(item.get("days_on_market")), months_on_market=_int(item.get("months_on_market")),
        days_since_price_change=_int(item.get("days_since_price_change")),
        reduced_by_pct=_float(item.get("reduced_by")),
        price_history=[{"date": h["date"], "price": _int(h["price"])} for h in item.get("price_history") or []],
        lists=[l["id"] for l in item.get("lists") or []], summary=summary,
        distance_miles=_float(item.get("distance_to")),
    )
