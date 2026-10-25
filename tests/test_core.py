import json
import os
import shutil
from pathlib import Path

import pytest

os.environ["FLIPWATCH_DB_URL"] = "sqlite://"  # in-memory, never touch the real DBs
os.environ["FLIPWATCH_REF_DB_URL"] = "sqlite://"

from flipwatch import finance  # noqa: E402
from flipwatch.config import FlipAssumptions  # noqa: E402
from flipwatch.db import Property, PropertyEvent, Session, init_db  # noqa: E402
from flipwatch.ingest import import_raw_file  # noqa: E402

RAW = Path(__file__).parent.parent / "data" / "raw" / "20261025T1545Z_sourced-properties_L35_p1.json"


def test_sdlt_higher_rates():
    assert finance.sdlt(150_000, 0.05) == 125_000 * 0.05 + 25_000 * 0.07
    assert finance.sdlt(150_000, 0) == 500
    assert finance.sdlt(39_999, 0.05) == 0
    assert finance.sdlt(300_000, 0) == 2_500 + 2_500


def test_max_purchase_hits_target_profit():
    a = FlipAssumptions()
    mpp = finance.max_purchase_price(240_000, 25_000, a=a)
    assert 39_000 < finance.deal(mpp, 240_000, 25_000, a).net_profit
    assert finance.deal(mpp + 1_000, 240_000, 25_000, a).net_profit < 40_000
    assert finance.max_purchase_price(60_000, 25_000, a=a) == 0


def test_required_gdv_inverts_max_purchase():
    gdv = finance.required_gdv(150_000, 25_000)
    assert abs(finance.max_purchase_price(gdv, 25_000) - 150_000) <= 1_500


def test_recommendation_bands():
    assert finance.recommend(150_000, 150_000, "HIGH", []) == "CALL AGENT"
    assert finance.recommend(160_000, 150_000, "HIGH", []) == "INVESTIGATE"
    assert finance.recommend(175_000, 150_000, "HIGH", []) == "WATCH"
    assert finance.recommend(210_000, 150_000, "HIGH", []) == "REJECT"
    assert finance.recommend(150_000, 150_000, "HIGH", ["non_standard_construction"]) == "REJECT"
    assert finance.recommend(150_000, 150_000, "LOW", []) == "INVESTIGATE"


def test_expected_purchase_price_for_auctions():
    assert finance.expected_purchase_price(100_000, []) == 100_000
    assert finance.expected_purchase_price(100_000, ["auction"]) == 125_000
    assert finance.expected_purchase_price(100_000, ["auction", "modern_method_of_auction"]) == 106_000
    assert finance.expected_purchase_price(200_000, ["modern_method_of_auction"]) == 210_800


def test_ingest_and_history(tmp_path):
    init_db()
    f = tmp_path / RAW.name
    shutil.copy(RAW, f)
    assert import_raw_file(f)["new"] == 10
    with pytest.raises(RuntimeError):
        import_raw_file(f)  # same file twice is refused

    # Second observation a week later: St Winifred Road reduced again, seen via the other portal id.
    doc = json.loads(f.read_text())
    doc["fetched_at"] = "2026-11-01T09:00:00Z"
    item = next(p for p in doc["response"]["properties"] if p["id"] == "R164749301")
    item["price"], item["days_on_market"] = 160_000, 389
    doc["response"]["properties"] = [item]
    f2 = tmp_path / "second.json"
    f2.write_text(json.dumps(doc))
    assert import_raw_file(f2)["new"] == 0
    with Session() as s:
        p = s.query(Property).filter_by(propertydata_id="R164749301").one()
        assert p.original_asking_price == 210_000 and p.current_asking_price == 160_000
        reds = s.query(PropertyEvent).filter_by(property_id=p.id, event_type="PRICE_REDUCTION").all()
        assert len(reds) == 5  # 4 back-filled from source history + 1 we observed


def test_auction_date_parsing():
    from datetime import date
    from flipwatch.signals import auction_date
    t = date(2026, 10, 25)
    assert auction_date("will be sold by auction on 3rd November 2026.", t) == date(2026, 11, 3)
    assert auction_date("offered by auction on 17 November 2026", t) == date(2026, 11, 17)
    assert auction_date("for sale by auction, vacant possession", t) is None


def test_epc_address_keys_match_land_registry():
    from flipwatch.db import SoldPrice
    from flipwatch.sources.epc import address_key, floor_area_sqm, lr_address_key
    sale = SoldPrice(saon="", paon="12A", street="BURLAND ROAD")
    assert lr_address_key(sale) == address_key("12a, Burland Road") == "12A BURLAND"
    flat = SoldPrice(saon="FLAT 1", paon="12", street="HIGH STREET")
    assert lr_address_key(flat) == address_key("Flat 1, 12 High Street")
    assert floor_area_sqm({"total_floor_area": 67}) == 67.0
    assert floor_area_sqm({"total_floor_area": {"value": 45.8}}) == 45.8
    assert floor_area_sqm({"total_floor_area": 0}) is None


def test_epc_enrich_and_match():
    from datetime import date
    from flipwatch.db import EpcCertificate, SoldPrice
    from flipwatch.sources import epc

    class FakeClient:
        calls = 0

        def search_postcode(self, pc):
            return [{"certificateNumber": "A", "addressLine1": "12 Burland Road", "registrationDate": "2019-03-01"},
                    {"certificateNumber": "B", "addressLine1": "12 Burland Road", "registrationDate": "2024-06-01"},
                    {"certificateNumber": "C", "addressLine1": "99 Burland Road", "registrationDate": "2020-01-01"}]

        def certificate(self, n):
            self.calls += 1
            return {"total_floor_area": {"A": 60, "B": 75}[n]}

    init_db()
    c = FakeClient()
    with Session.begin() as s:
        s.add(SoldPrice(transaction_id="t1", price=150_000, sale_date=date(2022, 5, 1), postcode="L26 9YJ",
                        outcode="L26", property_type="semi-detached", new_build=False, tenure="F", paon="12",
                        saon="", street="BURLAND ROAD", ppd_category="A", source="test"))
        assert epc.search_postcode(s, c, "L26 9YJ") == 3
        assert epc.search_postcode(s, c, "L26 9YJ") == 0  # cached
        assert epc.fetch_floor_areas(s, c, "L26 9YJ", {"12 BURLAND"}) == 2  # 99 is not needed
        assert epc.match_sales(s, {"L26 9YJ"}) == 1
        # the certificate in force at the 2022 sale (60 m2), not the later extension (75 m2)
        assert s.get(SoldPrice, "t1").floor_area_sqft == round(60 * epc.SQM_TO_SQFT)
        assert s.get(EpcCertificate, "C").floor_area_sqm is None


def test_gdv_per_sqft_size_matched():
    from datetime import date
    from flipwatch import comps
    base = dict(sale_date=date(2025, 1, 1), distance=0.3, same_street=False, weight=1.0)
    cs = [dict(base, price=round(sq * psf), floor_area_sqft=sq)
          for sq, psf in [(700, 280), (720, 290), (740, 300), (690, 270), (760, 310), (710, 295), (730, 285),
                          (705, 300)]]
    cs.append(dict(base, price=400_000, floor_area_sqft=1400))  # much larger house, excluded by size
    g = comps.gdv_per_sqft(cs, 721)
    assert g["n"] == 8 and g["size_band"] == 0.25
    assert 270 <= g["ppsf_low"] <= g["ppsf_base"] <= g["ppsf_high"] <= 310
    assert abs(g["gdv_base"] - g["ppsf_base"] * 721) < 1000
    assert g["confidence"] == "MEDIUM"
    assert comps.gdv_per_sqft(cs[:3], 721) is None


def test_refurb_scales_with_size_and_condition():
    assert finance.refurb_estimate(721, []) == (25_200, "modernise £35/sqft x 721 sqft")
    assert finance.refurb_estimate(721, ["derelict_poor_condition"])[0] == 39_700
    assert finance.refurb_estimate(300, [])[0] == 15_000
    assert finance.refurb_estimate(None, [])[0] == 25_000


def test_estimated_sqft_capped_by_bedrooms():
    from flipwatch import comps
    cs = [dict(floor_area_sqft=850, distance=0.1 * i) for i in range(6)]
    assert comps.estimated_sqft(cs) == 850
    assert comps.estimated_sqft(cs, 1) == 600
    assert comps.estimated_sqft(cs, 3) == 850
    assert comps.estimated_sqft(cs[:4], 3) is None
