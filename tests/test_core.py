import json
import os
import shutil
from pathlib import Path

import pytest

os.environ["FLIPWATCH_DB_URL"] = "sqlite://"  # in-memory, never touch the real DB

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
