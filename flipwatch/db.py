"""Relational schema. Portable SQLAlchemy types only, so SQLite -> PostgreSQL/Supabase is a URL change."""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import settings


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Property(Base):
    """One physical property, however many sources/portals list it."""
    __tablename__ = "properties"
    id: Mapped[int] = mapped_column(primary_key=True)
    propertydata_id: Mapped[str | None] = mapped_column(String(32), index=True)
    uprn: Mapped[str | None] = mapped_column(String(20), index=True)
    address: Mapped[str] = mapped_column(Text)
    precise_address: Mapped[str | None] = mapped_column(Text)
    postcode: Mapped[str | None] = mapped_column(String(10), index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    property_type: Mapped[str | None] = mapped_column(String(40))
    property_type_raw: Mapped[str | None] = mapped_column(String(40))
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    floor_area_sqft: Mapped[int | None] = mapped_column(Integer)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    first_listed_date: Mapped[str | None] = mapped_column(String(10))  # from source price history
    original_asking_price: Mapped[int | None] = mapped_column(Integer)
    current_asking_price: Mapped[int | None] = mapped_column(Integer)
    current_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    # Status confirmed by a person on the portal (SSTC / SOLD / WITHDRAWN). Overrides the feed.
    manual_status: Mapped[str | None] = mapped_column(String(20))
    manual_status_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_lists: Mapped[list | None] = mapped_column(JSON)
    risk_flags: Mapped[list | None] = mapped_column(JSON)
    motivation_flags: Mapped[list | None] = mapped_column(JSON)
    portal_url: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)

    sources: Mapped[list["ListingSource"]] = relationship(back_populates="property")


class ListingSource(Base):
    """A source-specific listing (PropertyData R…/Z… id, future feeds) mapped to a property."""
    __tablename__ = "listing_sources"
    __table_args__ = (UniqueConstraint("source", "source_ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    source: Mapped[str] = mapped_column(String(30))  # e.g. "propertydata"
    source_ref: Mapped[str] = mapped_column(String(64))  # e.g. "R164749301"
    portal: Mapped[str | None] = mapped_column(String(20))  # rightmove / zoopla
    url: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    property: Mapped[Property] = relationship(back_populates="sources")


class ListingObservation(Base):
    __tablename__ = "listing_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    listing_source_id: Mapped[int | None] = mapped_column(ForeignKey("listing_sources.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    asking_price: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(20))
    sstc: Mapped[bool | None] = mapped_column(Boolean)
    days_on_market: Mapped[int | None] = mapped_column(Integer)
    months_on_market: Mapped[int | None] = mapped_column(Integer)
    days_since_price_change: Mapped[int | None] = mapped_column(Integer)
    reduced_by_pct: Mapped[float | None] = mapped_column(Float)
    price_history: Mapped[list | None] = mapped_column(JSON)
    source_lists: Mapped[list | None] = mapped_column(JSON)
    raw_response_id: Mapped[int | None] = mapped_column(ForeignKey("raw_responses.id"))


class PropertyEvent(Base):
    __tablename__ = "property_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event_type: Mapped[str] = mapped_column(String(30), index=True)
    event_data: Mapped[dict | None] = mapped_column(JSON)


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    analysis_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    asking_price: Mapped[int | None] = mapped_column(Integer)
    expected_purchase_price: Mapped[int | None] = mapped_column(Integer)  # auction uplift / MMoA fee
    gdv_low: Mapped[int | None] = mapped_column(Integer)
    gdv_base: Mapped[int | None] = mapped_column(Integer)
    gdv_high: Mapped[int | None] = mapped_column(Integer)
    gdv_source: Mapped[str | None] = mapped_column(Text)
    refurb_cost: Mapped[int | None] = mapped_column(Integer)
    sdlt: Mapped[int | None] = mapped_column(Integer)
    legal_cost: Mapped[int | None] = mapped_column(Integer)
    finance_cost: Mapped[int | None] = mapped_column(Integer)
    holding_cost: Mapped[int | None] = mapped_column(Integer)
    selling_cost: Mapped[int | None] = mapped_column(Integer)
    contingency: Mapped[int | None] = mapped_column(Integer)
    expected_profit_at_asking: Mapped[int | None] = mapped_column(Integer)
    maximum_purchase_price_for_target: Mapped[int | None] = mapped_column(Integer)
    offer_gap: Mapped[int | None] = mapped_column(Integer)
    opening_offer: Mapped[int | None] = mapped_column(Integer)
    opportunity_score: Mapped[float | None] = mapped_column(Float)
    recommendation: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[str | None] = mapped_column(String(10))
    risks: Mapped[list | None] = mapped_column(JSON)
    assumptions: Mapped[dict | None] = mapped_column(JSON)


class Comparable(Base):
    __tablename__ = "comparables"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("analyses.id"))
    comparable_address: Mapped[str] = mapped_column(Text)
    sale_price: Mapped[int] = mapped_column(Integer)
    sale_date: Mapped[Date | None] = mapped_column(Date)
    property_type: Mapped[str | None] = mapped_column(String(40))
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    floor_area_sqft: Mapped[int | None] = mapped_column(Integer)
    distance_miles: Mapped[float | None] = mapped_column(Float)
    price_per_sqft: Mapped[float | None] = mapped_column(Float)
    similarity_score: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(30))


class SoldPrice(Base):
    """Local copy of HM Land Registry Price Paid Data for the search region (free comparables)."""
    __tablename__ = "sold_prices"
    transaction_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    price: Mapped[int] = mapped_column(Integer)
    sale_date: Mapped[Date] = mapped_column(Date, index=True)
    postcode: Mapped[str] = mapped_column(String(10), index=True)
    outcode: Mapped[str] = mapped_column(String(5), index=True)
    property_type: Mapped[str] = mapped_column(String(15))
    new_build: Mapped[bool] = mapped_column(Boolean)
    tenure: Mapped[str] = mapped_column(String(1))
    paon: Mapped[str | None] = mapped_column(String(100))
    saon: Mapped[str | None] = mapped_column(String(100))
    street: Mapped[str | None] = mapped_column(String(100), index=True)
    locality: Mapped[str | None] = mapped_column(String(100))
    town: Mapped[str | None] = mapped_column(String(100))
    district: Mapped[str | None] = mapped_column(String(100))
    ppd_category: Mapped[str] = mapped_column(String(1))  # A standard, B repossession/BTL/etc.
    source: Mapped[str] = mapped_column(String(10), index=True)
    floor_area_sqft: Mapped[int | None] = mapped_column(Integer)  # filled from EPC later
    uprn: Mapped[str | None] = mapped_column(String(20))


class Postcode(Base):
    """Cached postcode / outcode centroids (postcodes.io, free)."""
    __tablename__ = "postcodes"
    postcode: Mapped[str] = mapped_column(String(10), primary_key=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    is_outcode: Mapped[bool] = mapped_column(Boolean, default=False)


class RawResponse(Base):
    """Every API payload, kept for debugging and re-processing. Never contains the API key."""
    __tablename__ = "raw_responses"
    id: Mapped[int] = mapped_column(primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(30))
    endpoint: Mapped[str] = mapped_column(String(60))
    request_params: Mapped[dict | None] = mapped_column(JSON)
    file_path: Mapped[str | None] = mapped_column(Text, unique=True)
    payload: Mapped[dict | None] = mapped_column(JSON)


class ApiCall(Base):
    """Credit ledger: one row per paid call."""
    __tablename__ = "api_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    source: Mapped[str] = mapped_column(String(30))
    endpoint: Mapped[str] = mapped_column(String(60))
    via: Mapped[str] = mapped_column(String(10))  # rest / mcp
    credits_estimated: Mapped[int | None] = mapped_column(Integer)
    credits_reported: Mapped[int | None] = mapped_column(Integer)
    result_count: Mapped[int | None] = mapped_column(Integer)
    raw_response_id: Mapped[int | None] = mapped_column(ForeignKey("raw_responses.id"))


REFERENCE_TABLES = [SoldPrice.__table__, Postcode.__table__]

engine = create_engine(settings.db_url)
ref_engine = create_engine(settings.ref_db_url)
Session = sessionmaker(binds={Base: engine, SoldPrice: ref_engine, Postcode: ref_engine},
                       expire_on_commit=False)


def init_db():
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables
                                             if t not in REFERENCE_TABLES])
    Base.metadata.create_all(ref_engine, tables=REFERENCE_TABLES)
    _add_missing_columns()


def _add_missing_columns():
    """Minimal forward migration: add new nullable columns to existing tables."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if table in REFERENCE_TABLES or not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in have and col.nullable:
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} '
                                      f'{col.type.compile(engine.dialect)}'))
