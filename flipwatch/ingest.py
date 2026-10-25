"""Store normalised listings: dedupe, append an observation, and emit history events."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from . import signals
from .db import ApiCall, ListingObservation, ListingSource, Property, PropertyEvent, RawResponse, Session
from .sources import propertydata
from .sources.base import Listing

DOM_THRESHOLDS = (60, 90, 120)
EXCLUDED_TYPES = {"flat", "land", "commercial"}


def _street(address: str) -> str:
    first = address.split(",")[0].lower()
    return re.sub(r"^\s*\d+[a-z]?[\.,]?\s+", "", first).strip()  # drop leading house number


def _find_existing(s, l: Listing) -> tuple[Property | None, ListingSource | None]:
    src = s.scalar(select(ListingSource).where(ListingSource.source == l.source,
                                               ListingSource.source_ref == l.source_ref))
    if src:
        return src.property, src
    # Same property on another portal: same postcode + beds + street.
    if l.postcode:
        for p in s.scalars(select(Property).where(Property.postcode == l.postcode,
                                                  Property.bedrooms == l.bedrooms)):
            if _street(p.address) == _street(l.address):
                return p, None
    return None, None


def _event(s, prop, etype, data=None, when=None):
    s.add(PropertyEvent(property_id=prop.id, event_type=etype, event_data=data,
                        event_date=when or datetime.now(timezone.utc)))


def ingest_listing(s, l: Listing, raw_id: int | None, now: datetime) -> tuple[Property, bool]:
    motivation, risks = signals.extract(l.summary, l.lists)
    prop, src = _find_existing(s, l)
    is_new = prop is None
    prev_price = prev_dom = None
    prev_lists: set[str] = set()
    if is_new:
        hist = l.price_history
        prop = Property(propertydata_id=l.source_ref if l.source == "propertydata" else None,
                        address=l.address, postcode=l.postcode, first_seen=now,
                        first_listed_date=hist[0]["date"] if hist else None,
                        original_asking_price=hist[0]["price"] if hist else l.price)
        s.add(prop)
        s.flush()
    else:
        prev_price = prop.current_asking_price
        prev_lists = set(prop.current_lists or [])
        last_obs = s.scalar(select(ListingObservation).where(ListingObservation.property_id == prop.id)
                            .order_by(ListingObservation.observed_at.desc()))
        prev_dom = last_obs.days_on_market if last_obs else None
        if prop.current_status == "REMOVED":
            _event(s, prop, "RELISTED", {"price": l.price}, now)

    if src is None:
        src = ListingSource(property_id=prop.id, source=l.source, source_ref=l.source_ref,
                            portal=l.portal, url=l.url, first_seen=now)
        s.add(src)
        s.flush()
        if not is_new:
            _event(s, prop, "DUPLICATE_SOURCE_LINKED", {"source_ref": l.source_ref, "portal": l.portal}, now)
    src.last_seen = now

    # Refresh identity/state (precise_address and sqft appear on some portals only).
    prop.precise_address = l.precise_address or prop.precise_address
    prop.latitude, prop.longitude = l.latitude, l.longitude
    prop.property_type, prop.property_type_raw = l.property_type, l.property_type_raw
    prop.bedrooms = l.bedrooms
    prop.floor_area_sqft = l.floor_area_sqft or prop.floor_area_sqft
    prop.portal_url = l.url
    prop.summary = l.summary
    prop.current_asking_price = l.price
    prop.current_lists = sorted(set(l.lists))
    prop.motivation_flags, prop.risk_flags = motivation, risks
    prop.current_status = "SSTC" if l.sstc else "ACTIVE"
    prop.last_seen = now
    if l.property_type in EXCLUDED_TYPES:
        prop.current_status = "EXCLUDED"
    elif (when := signals.auction_date(l.summary, now.date())) and when < now.date():
        prop.current_status = "AUCTION_PASSED"
    elif prop.manual_status:
        # A person checked the portal; the feed's SSTC flag lags. Keep their status, but surface
        # a price change after the check since that can mean the sale fell through.
        prop.current_status = prop.manual_status
        if prev_price and l.price and l.price != prev_price:
            _event(s, prop, "PRICE_CHANGE_AFTER_MANUAL_STATUS",
                   {"manual_status": prop.manual_status, "from": prev_price, "to": l.price}, now)

    s.add(ListingObservation(
        property_id=prop.id, listing_source_id=src.id, observed_at=now, asking_price=l.price,
        status=prop.current_status, sstc=l.sstc, days_on_market=l.days_on_market,
        months_on_market=l.months_on_market, days_since_price_change=l.days_since_price_change,
        reduced_by_pct=l.reduced_by_pct, price_history=l.price_history, source_lists=l.lists,
        raw_response_id=raw_id))

    if is_new:
        _event(s, prop, "FIRST_SEEN", {"price": l.price, "lists": l.lists}, now)
        # Back-fill price changes the source already knows about, dated when they happened.
        for a, b in zip(l.price_history, l.price_history[1:]):
            if a["price"] != b["price"]:
                etype = "PRICE_REDUCTION" if b["price"] < a["price"] else "PRICE_INCREASE"
                _event(s, prop, etype, {"from": a["price"], "to": b["price"], "backfilled": True},
                       datetime.fromisoformat(b["date"]).replace(tzinfo=timezone.utc))
    elif prev_price and l.price and l.price != prev_price:
        etype = "PRICE_REDUCTION" if l.price < prev_price else "PRICE_INCREASE"
        _event(s, prop, etype, {"from": prev_price, "to": l.price}, now)

    for t in DOM_THRESHOLDS:
        if l.days_on_market and l.days_on_market >= t and (prev_dom is None or prev_dom < t):
            _event(s, prop, f"DAYS_{t}", {"days_on_market": l.days_on_market, "initial": is_new}, now)
    if "back-on-market" in l.lists and "back-on-market" not in prev_lists:
        _event(s, prop, "BACK_ON_MARKET", {"initial": is_new}, now)
    return prop, is_new


def import_raw_file(path: Path) -> dict:
    """Load a saved raw PropertyData response (from REST or MCP), log credits, ingest listings.
    Idempotent per file: re-importing the same file is refused."""
    doc = json.loads(path.read_text())
    resp = doc["response"]
    now = datetime.fromisoformat(doc["fetched_at"].replace("Z", "+00:00"))
    with Session.begin() as s:
        if s.scalar(select(RawResponse).where(RawResponse.file_path == str(path))):
            raise RuntimeError(f"{path.name} already imported")
        raw = RawResponse(fetched_at=now, source=doc["source"], endpoint=doc["endpoint"],
                          request_params=doc["request"], file_path=str(path), payload=resp)
        s.add(raw)
        s.flush()
        s.add(ApiCall(called_at=now, source=doc["source"], endpoint=doc["endpoint"],
                      via=doc.get("fetched_via", "rest"), credits_reported=resp.get("api_calls_cost"),
                      result_count=resp.get("result_count"), raw_response_id=raw.id,
                      run_id=doc.get("run_id")))
        new = 0
        for item in resp.get("properties", []):
            _, is_new = ingest_listing(s, propertydata.normalise(item), raw.id, now)
            new += is_new
        requested = int(doc["request"].get("results", 10))
        return dict(returned=len(resp.get("properties", [])), new=new,
                    credits=resp.get("api_calls_cost"),
                    page_full=len(resp.get("properties", [])) >= requested)
