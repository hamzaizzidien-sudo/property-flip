"""Settings. Secrets come from the environment / .env only — never hard-code them."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass
class SearchConfig:
    # Approximate centre of the L35 district (Whiston / Prescot / Rainhill).
    location: str = "53.4165,-2.7880"
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


@dataclass
class Settings:
    db_url: str = os.getenv("FLIPWATCH_DB_URL", f"sqlite:///{ROOT / 'data' / 'flipwatch.db'}")
    raw_dir: Path = ROOT / "data" / "raw"
    output_dir: Path = ROOT / "output"
    propertydata_api_key: str | None = os.getenv("PROPERTYDATA_API_KEY")
    max_credits_per_run: int = int(os.getenv("FLIPWATCH_MAX_CREDITS_PER_RUN", "10"))
    search: SearchConfig = field(default_factory=SearchConfig)
    flip: FlipAssumptions = field(default_factory=FlipAssumptions)


settings = Settings()
