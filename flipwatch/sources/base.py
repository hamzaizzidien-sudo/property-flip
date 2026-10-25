"""Source-agnostic listing record. Every feed (PropertyData, a future portal feed, auction sites)
normalises into this so ingestion, history and scoring never depend on one provider."""
from dataclasses import dataclass, field


@dataclass
class Listing:
    source: str
    source_ref: str
    address: str
    postcode: str | None
    price: int | None
    precise_address: str | None = None
    portal: str | None = None
    url: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    property_type: str | None = None  # normalised: detached / semi-detached / terraced / bungalow / flat / other
    property_type_raw: str | None = None
    bedrooms: int | None = None
    floor_area_sqft: int | None = None
    sstc: bool | None = None
    days_on_market: int | None = None
    months_on_market: int | None = None
    days_since_price_change: int | None = None
    reduced_by_pct: float | None = None
    price_history: list[dict] = field(default_factory=list)  # [{"date": "YYYY-MM-DD", "price": int}]
    lists: list[str] = field(default_factory=list)
    summary: str | None = None
    distance_miles: float | None = None
