"""Settings. Secrets come from the environment / .env only — never hard-code them."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass
class SearchConfig:
    # L35 outcode centroid (postcodes.io): Whiston / Prescot / Rainhill.
    location: str = "53.4144,-2.7839"
    radius_miles: int = 15
    max_price: int = 180_000
    standardised_type: str = "!flat"  # keep bungalows; land is filtered locally
    exclude_sstc: int = 1
    priority_lists: tuple = (
        "unmodernised-properties",
        "reduced-properties",
        "slow-to-sell-properties",
        "back-on-market",
        "auction-properties",
    )
    secondary_lists: tuple = (
        "properties-with-no-chain",
        "repossessed-properties",
        "cash-buyers-only-properties",
        "quick-sale-properties",
        "derelict-properties",
    )


@dataclass
class RunProfile:
    """A scheduled discovery run. `max_age` filters on PropertyData's last-update age (verified
    2026-10-25: returns listings with any change in the last N days — new, reduced, relisted —
    not listings that are N days old)."""
    radius_miles: int
    results_per_page: int
    max_credits: int
    max_age: int | None = None


RUN_PROFILES = {
    "daily": RunProfile(radius_miles=8, results_per_page=30, max_credits=5, max_age=1),
    "weekly": RunProfile(radius_miles=8, results_per_page=100, max_credits=50),
}


@dataclass
class FlipAssumptions:
    """Default deal costs. All overridable per analysis. Figures are first-pass NW England guesses."""
    target_profit: int = 40_000
    buy_legal: int = 1_500
    survey: int = 600
    sale_legal: int = 1_200
    agent_fee_pct: float = 0.012 * 1.2  # 1.2% + VAT
    months_held: int = 6
    holding_per_month: int = 350  # council tax, insurance, utilities
    finance_ltv: float = 0.0  # 0 = cash purchase; e.g. 0.70 for bridging
    finance_rate_monthly: float = 0.0085
    finance_arrangement_pct: float = 0.02
    contingency_pct_of_refurb: float = 0.10
    default_refurb: int = 25_000
    sdlt_surcharge: float = 0.05  # higher rates for additional dwellings (from 31 Oct 2024)
    opening_offer_discount: float = 0.08
    auction_uplift: float = 1.25  # traditional auction lots typically sell above guide
    mmoa_fee_pct: float = 0.045 * 1.2  # Modern Method of Auction buyer reservation fee + VAT
    mmoa_fee_min: int = 6_000


@dataclass
class Settings:
    # History DB (listings, events, analyses) is committed to git so scheduled runs keep history.
    db_url: str = os.getenv("FLIPWATCH_DB_URL", f"sqlite:///{ROOT / 'data' / 'flipwatch.db'}")
    # Reference DB (Land Registry, postcodes) is large and rebuildable, so it is git-ignored.
    ref_db_url: str = os.getenv("FLIPWATCH_REF_DB_URL", f"sqlite:///{ROOT / 'data' / 'reference.db'}")
    raw_dir: Path = ROOT / "data" / "raw"
    output_dir: Path = ROOT / "output"
    propertydata_api_key: str | None = os.getenv("PROPERTYDATA_API_KEY")
    max_credits_per_run: int = int(os.getenv("FLIPWATCH_MAX_CREDITS_PER_RUN", "10"))
    credit_allowance: int = int(os.getenv("FLIPWATCH_CREDIT_ALLOWANCE", "500"))  # trial account
    search: SearchConfig = field(default_factory=SearchConfig)
    flip: FlipAssumptions = field(default_factory=FlipAssumptions)


settings = Settings()
