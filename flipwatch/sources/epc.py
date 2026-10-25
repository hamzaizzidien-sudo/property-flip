"""EPC floor areas -> `epc_certificates` (reference DB) and `sold_prices.floor_area_sqft` (free).

Uses the MHCLG "Get energy performance of buildings data" API, which replaced
epc.opendatacommunities.org (the old host now 301s there). Auth is a bearer token from the
service's account page, read from EPC_API_KEY; EPC_EMAIL is no longer needed. Search returns
certificate headers only, so floor area needs one /api/certificate call per certificate; both
are cached, so each postcode and certificate is fetched once. Limit 6000 requests / 5 min.
Contains EPC data © Crown copyright, licensed under the Open Government Licence v3.0.
"""
import os
import re
import time
from datetime import datetime, timezone

import requests
from sqlalchemy import select

from ..db import EpcCertificate, EpcPostcode, SoldPrice

BASE = "https://api.get-energy-performance-data.communities.gov.uk"
SQM_TO_SQFT = 10.7639


class EpcClient:
    def __init__(self, token: str | None = None):
        token = token or os.getenv("EPC_API_KEY")
        if not token:
            raise RuntimeError("EPC_API_KEY is not set")
        self.http = requests.Session()
        self.http.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _get(self, path: str, params: dict):
        for attempt in range(3):
            r = self.http.get(f"{BASE}{path}", params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(30 * (attempt + 1))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        r.raise_for_status()

    def search_postcode(self, postcode: str) -> list[dict]:
        rows, page = [], 1
        while True:
            body = self._get("/api/domestic/search", {"postcode": postcode, "page_size": 5000,
                                                      "current_page": page})
            if not body:
                return rows
            data = body.get("data", body)
            # The documented example is not valid JSON, so accept the list wherever it sits.
            items = data if isinstance(data, list) else next(
                (v for v in data.values() if isinstance(v, list)), [])
            rows += items
            pag = body.get("pagination") or (data.get("pagination") if isinstance(data, dict) else None) or {}
            if not pag.get("nextPage"):
                return rows
            page = pag["nextPage"]

    def certificate(self, number: str) -> dict | None:
        body = self._get("/api/certificate", {"certificate_number": number})
        return body.get("data") if body else None


def address_key(text: str) -> str:
    """Postcode-local key: the house number/name plus the first word of the street.
    '12a, Burland Road' and '12A BURLAND ROAD' -> '12A BURLAND'."""
    t = re.sub(r"[^A-Z0-9 ]", " ", (text or "").upper())
    t = re.sub(r"\b(FLAT|APARTMENT|APT)\s+", "FLAT", t)
    words = t.split()
    return " ".join(words[:2]) if words else ""


def lr_address_key(sale: SoldPrice) -> str:
    return address_key(" ".join(filter(None, [sale.saon, sale.paon, sale.street])))


def epc_address_key(cert: EpcCertificate) -> str:
    return address_key(cert.address)


def floor_area_sqm(data: dict) -> float | None:
    v = data.get("total_floor_area")
    if isinstance(v, dict):
        v = v.get("value")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if 10 <= v <= 1000 else None


def search_postcode(s, client: EpcClient, postcode: str) -> int:
    """Cache certificate headers for a postcode once. Returns headers added."""
    if s.get(EpcPostcode, postcode):
        return 0
    added = 0
    for h in client.search_postcode(postcode):
        number = h.get("certificateNumber")
        if not number or s.get(EpcCertificate, number):
            continue
        s.add(EpcCertificate(
            certificate_number=number, postcode=postcode,
            address=", ".join(filter(None, [h.get(f"addressLine{i}") for i in range(1, 5)])),
            uprn=str(h["uprn"]) if h.get("uprn") else None,
            registration_date=datetime.strptime(h["registrationDate"][:10], "%Y-%m-%d").date()
            if h.get("registrationDate") else None,
            band=h.get("currentEnergyEfficiencyBand")))
        added += 1
    s.add(EpcPostcode(postcode=postcode, searched_at=datetime.now(timezone.utc)))
    s.flush()
    return added


def fetch_floor_areas(s, client: EpcClient, postcode: str, keys: set[str]) -> int:
    """Fetch full certificates (for floor area) only for addresses we need. Returns calls made."""
    calls = 0
    for c in s.scalars(select(EpcCertificate).where(EpcCertificate.postcode == postcode,
                                                    EpcCertificate.detail_fetched.is_(False))):
        if epc_address_key(c) not in keys:
            continue
        c.floor_area_sqm = floor_area_sqm(client.certificate(c.certificate_number) or {})
        c.detail_fetched = True
        calls += 1
    s.flush()
    return calls


def match_sales(s, postcodes: set[str]) -> int:
    """Copy floor areas onto sold_prices by postcode + address key. Where a home has several
    certificates, the latest one registered before the sale (else the earliest after) wins."""
    certs = {}
    for c in s.scalars(select(EpcCertificate).where(EpcCertificate.postcode.in_(postcodes),
                                                    EpcCertificate.floor_area_sqm.is_not(None))):
        certs.setdefault((c.postcode, epc_address_key(c)), []).append(c)
    matched = 0
    for sale in s.scalars(select(SoldPrice).where(SoldPrice.postcode.in_(postcodes))):
        cands = certs.get((sale.postcode, lr_address_key(sale)))
        if not cands:
            continue
        before = [c for c in cands if c.registration_date and c.registration_date <= sale.sale_date]
        best = max(before, key=lambda c: c.registration_date) if before else \
            min(cands, key=lambda c: c.registration_date or sale.sale_date)
        sale.floor_area_sqft = round(best.floor_area_sqm * SQM_TO_SQFT)
        matched += 1
    return matched


def subject_floor_area(s, prop) -> tuple[int | None, str]:
    """Floor area for a listing: the portal's figure, else its EPC by address, else none."""
    if prop.floor_area_sqft:
        return prop.floor_area_sqft, "listing"
    key = address_key((prop.precise_address or "").split(",")[0]) if prop.precise_address else ""
    if key and re.match(r"\d", key) and prop.postcode:
        for c in s.scalars(select(EpcCertificate).where(EpcCertificate.postcode == prop.postcode)
                           .order_by(EpcCertificate.registration_date.desc())):
            if c.floor_area_sqm and epc_address_key(c) == key:
                return round(c.floor_area_sqm * SQM_TO_SQFT), "epc"
    return None, "none"
