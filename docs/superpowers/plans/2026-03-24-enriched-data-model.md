# Enriched Data Model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a three-table enrichment layer (`listing_detail`, `listing_enrichment`, `listing_score`) with a three-stage pipeline (detail fetch → LLM enrichment → composite scoring) to capture logistics, seller psychology, temporal signals, product completeness, and actionable flip scores.

**Architecture:** Connectors write raw detail data to `listing_detail` via a selective 2nd-pass fetch (only promising listings). An hourly ARQ batch job calls Gemini Flash to enrich listings into `listing_enrichment`. A post-enrichment scoring job materializes composite action scores (`arbitrage_spread_eur`, `net_roi_pct`, `risk_adjusted_confidence`) into `listing_score`. The existing M2 opportunity score stays for ranking/alerting; new scores layer alongside for buy decisions.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0, Alembic, ARQ, FastAPI, Pydantic, Google Generative AI (Gemini Flash), pytest

**Spec:** `docs/superpowers/specs/2026-03-24-enriched-data-model-design.md`

---

## File Structure

### New files to create:
| File | Responsibility |
|------|---------------|
| `migrations/versions/0007_enrichment_tables.py` | Alembic migration for 3 new tables |
| `libs/common/condition.py` | Shared condition normalization utility |
| `ingestion/detail_fetch.py` | 2nd-pass detail fetch orchestration + candidate selection |
| `ingestion/enrichment.py` | LLM enrichment batch job logic |
| `ingestion/enrichment_prompt.py` | Structured LLM prompt template + response parsing |
| `ingestion/composite_scoring.py` | Composite score computation (spread, ROI, confidence) |
| `tests/unit/test_condition.py` | Condition normalization tests |
| `tests/unit/test_detail_fetch.py` | Candidate selection logic tests |
| `tests/unit/test_enrichment_prompt.py` | LLM prompt/response parsing tests |
| `tests/unit/test_composite_scoring.py` | Score formula arithmetic tests |
| `tests/smoke/test_06_detail_fetch.py` | Real data detail fetch tests per connector |
| `tests/smoke/test_07_enrichment.py` | Enrichment structural + golden set tests |
| `tests/smoke/test_08_composite_scoring.py` | Score business logic on real data |

### Existing files to modify:
| File | Changes |
|------|---------|
| `libs/common/models.py` | Add ORM models: `ListingDetail`, `ListingEnrichment`, `ListingScore`; add `ListingDetail` Pydantic dataclass |
| `libs/common/settings.py` | Add enrichment config settings |
| `ingestion/connectors/ebay.py` | Add `fetch_detail()` function |
| `ingestion/connectors/leboncoin_api.py` | Add `fetch_detail()` method (primary) |
| `ingestion/connectors/leboncoin.py` | Add `fetch_detail()` method (delegates to API connector) |
| `ingestion/connectors/vinted_api.py` | Add `fetch_detail()` method (primary) |
| `ingestion/connectors/vinted.py` | Add `fetch_detail()` method (delegates to API connector) |
| `ingestion/ingestion.py` | Call detail fetch after 1st-pass persist |
| `ingestion/worker.py` | Register enrichment + scoring cron jobs |
| `backend/routers/health.py` | Add enrichment freshness monitoring |

---

## Phase 1: Foundation (Schema + Raw Storage)

### Task 1: Shared Condition Normalization Utility

Extract condition normalization from all connectors into a single shared function. The scoring engine depends on this.

**Files:**
- Create: `libs/common/condition.py`
- Create: `tests/unit/test_condition.py`
- Modify: `ingestion/connectors/ebay.py:126-143` (remove `normalize_condition`, import shared)
- Modify: `ingestion/connectors/leboncoin.py:26-46` (remove `normalize_condition_leboncoin`, import shared)
- Modify: `ingestion/connectors/leboncoin_api.py:156+` (remove `normalize_condition_leboncoin`, import shared)
- Modify: `ingestion/connectors/vinted.py:39-71` (remove `normalize_condition_vinted`, import shared)
- Modify: `ingestion/connectors/vinted_api.py:153-172` (remove `normalize_condition_vinted`, import shared)

- [ ] **Step 1: Write failing tests for shared condition normalization**

```python
# tests/unit/test_condition.py
import pytest
from libs.common.condition import normalize_condition


@pytest.mark.parametrize(
    "raw, expected",
    [
        # French
        ("Neuf", "new"),
        ("neuf avec étiquette", "new"),
        ("neuf sans étiquette", "new"),
        ("Très bon état", "like_new"),
        ("très bon état", "like_new"),
        ("comme neuf", "like_new"),
        ("Bon état", "good"),
        ("bon état", "good"),
        ("Satisfaisant", "fair"),
        ("État satisfaisant", "fair"),
        # English
        ("Brand New", "new"),
        ("new", "new"),
        ("NIB", "new"),
        ("Like New", "like_new"),
        ("Excellent", "like_new"),
        ("Mint", "like_new"),
        ("Very Good", "good"),
        ("Good", "good"),
        ("Acceptable", "fair"),
        ("Fair", "fair"),
        ("Poor", "fair"),
        # Edge cases
        ("", None),
        (None, None),
        ("unknown garbage", None),
        # Accented chars
        ("très bon état", "like_new"),
        ("Neuf avec étiquette", "new"),
    ],
)
def test_normalize_condition(raw: str | None, expected: str | None) -> None:
    assert normalize_condition(raw) == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_condition.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'libs.common.condition'`

- [ ] **Step 3: Implement shared condition normalization**

```python
# libs/common/condition.py
"""Shared condition normalization across all marketplace connectors."""
from __future__ import annotations

import re
import unicodedata


def _strip_accents(text: str) -> str:
    """Remove accent characters for matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_condition(raw: str | None) -> str | None:
    """Normalize raw condition string to: new, like_new, good, fair, or None.

    Handles French and English marketplace condition strings.
    Returns None if the string cannot be mapped.
    """
    if not raw or not raw.strip():
        return None

    text = _strip_accents(raw.strip().lower())

    # Order matters: check most specific patterns first
    new_patterns = [
        "brand new", "neuf avec etiquette", "neuf sans etiquette",
        "nouveau", "nib", "bnib", "neuf", "new",
    ]
    like_new_patterns = [
        "tres bon etat", "comme neuf", "like new", "excellent",
        "mint", "very good condition",
    ]
    good_patterns = [
        "bon etat", "good", "bien", "used",
    ]
    fair_patterns = [
        "etat satisfaisant", "satisfaisant", "acceptable",
        "fair", "poor", "worn",
    ]

    for pattern in new_patterns:
        if pattern in text:
            return "new"
    for pattern in like_new_patterns:
        if pattern in text:
            return "like_new"
    for pattern in good_patterns:
        if pattern in text:
            return "good"
    for pattern in fair_patterns:
        if pattern in text:
            return "fair"

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_condition.py -v`
Expected: All PASS

- [ ] **Step 5: Update connectors to use shared function**

In each connector file, replace the local `normalize_condition*` function with an import:

```python
from libs.common.condition import normalize_condition
```

For each connector:
- `ingestion/connectors/ebay.py`: Remove `normalize_condition()` (lines 126–143). Update `parse_ebay_response()` to call `normalize_condition(condition_raw)`.
- `ingestion/connectors/leboncoin.py`: Remove `normalize_condition_leboncoin()` (lines 26–46). Update references to call `normalize_condition()`.
- `ingestion/connectors/leboncoin_api.py`: Remove `normalize_condition_leboncoin()`. Update references.
- `ingestion/connectors/vinted.py`: Remove `normalize_condition_vinted()` (lines 39–71). Update references.
- `ingestion/connectors/vinted_api.py`: Remove `normalize_condition_vinted()` (lines 153–172). Update references.

- [ ] **Step 6: Run existing tests to verify no regressions**

Run: `uv run pytest tests/ -v --ignore=tests/smoke`
Expected: All existing tests pass

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add libs/common/condition.py tests/unit/test_condition.py ingestion/connectors/
git commit -m "refactor: extract shared condition normalization utility"
```

---

### Task 2: Alembic Migration — Three New Tables

**Files:**
- Create: `migrations/versions/0007_enrichment_tables.py`

- [ ] **Step 1: Write the migration**

```python
# migrations/versions/0007_enrichment_tables.py
"""Create listing_detail, listing_enrichment, listing_score tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # listing_detail: raw data from detail page fetches
    op.create_table(
        "listing_detail",
        sa.Column("detail_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "obs_id",
            sa.BigInteger,
            sa.ForeignKey("listing_observation.obs_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("description_length", sa.Integer, nullable=True),
        sa.Column("photo_urls", ARRAY(sa.Text), nullable=True),
        sa.Column("photo_count", sa.Integer, nullable=True),
        sa.Column("local_pickup_only", sa.Boolean, nullable=True),
        sa.Column("negotiation_enabled", sa.Boolean, nullable=True),
        sa.Column(
            "original_posted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("seller_account_age_days", sa.Integer, nullable=True),
        sa.Column("seller_transaction_count", sa.Integer, nullable=True),
        sa.Column("view_count", sa.Integer, nullable=True),
        sa.Column("favorite_count", sa.Integer, nullable=True),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_listing_detail_fetched_at", "listing_detail", ["fetched_at"])

    # listing_enrichment: LLM-derived analysis
    op.create_table(
        "listing_enrichment",
        sa.Column("enrichment_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "obs_id",
            sa.BigInteger,
            sa.ForeignKey("listing_observation.obs_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("urgency_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("urgency_keywords", ARRAY(sa.Text), nullable=True),
        sa.Column("has_original_box", sa.Boolean, nullable=True),
        sa.Column("has_receipt_or_invoice", sa.Boolean, nullable=True),
        sa.Column("accessories_included", ARRAY(sa.Text), nullable=True),
        sa.Column("accessories_completeness", sa.Numeric(3, 2), nullable=True),
        sa.Column("photo_quality_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("listing_quality_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("condition_confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("fakeness_probability", sa.Numeric(3, 2), nullable=True),
        sa.Column("seller_motivation_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("llm_model", sa.Text, nullable=True),
        sa.Column("llm_raw_response", JSONB, nullable=True),
        sa.Column(
            "enriched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("cost_tokens", sa.Integer, nullable=True),
    )
    op.create_index(
        "ix_listing_enrichment_enriched_at", "listing_enrichment", ["enriched_at"]
    )

    # listing_score: materialized composite action scores
    op.create_table(
        "listing_score",
        sa.Column("score_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "obs_id",
            sa.BigInteger,
            sa.ForeignKey("listing_observation.obs_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product_template.product_id"),
            nullable=False,
        ),
        sa.Column("arbitrage_spread_eur", sa.Numeric, nullable=True),
        sa.Column("net_roi_pct", sa.Numeric, nullable=True),
        sa.Column("risk_adjusted_confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("acquisition_cost_eur", sa.Numeric, nullable=True),
        sa.Column("estimated_sale_price_eur", sa.Numeric, nullable=True),
        sa.Column("estimated_sell_fees_eur", sa.Numeric, nullable=True),
        sa.Column("estimated_sell_shipping_eur", sa.Numeric, nullable=True),
        sa.Column("days_on_market", sa.Integer, nullable=True),
        sa.Column("score_breakdown", JSONB, nullable=True),
        sa.Column(
            "scored_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_listing_score_product_confidence",
        "listing_score",
        ["product_id", sa.text("risk_adjusted_confidence DESC")],
    )
    op.create_index(
        "ix_listing_score_product_spread",
        "listing_score",
        ["product_id", sa.text("arbitrage_spread_eur DESC")],
    )


def downgrade() -> None:
    op.drop_table("listing_score")
    op.drop_table("listing_enrichment")
    op.drop_table("listing_detail")
```

- [ ] **Step 2: Apply migration locally**

Run: `uv run alembic upgrade head`
Expected: `0006 -> 0007 ... done`

- [ ] **Step 3: Verify tables exist**

Run: `uv run python -c "from libs.common.db import engine; from sqlalchemy import inspect; print(inspect(engine).get_table_names())"`
Expected: Output includes `listing_detail`, `listing_enrichment`, `listing_score`

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/0007_enrichment_tables.py
git commit -m "feat(db): add listing_detail, listing_enrichment, listing_score tables"
```

---

### Task 3: ORM Models + Pydantic Dataclass

**Files:**
- Modify: `libs/common/models.py` (add after `ConnectorAudit` class, ~line 242)

- [ ] **Step 1: Add ORM models and Pydantic dataclass to models.py**

Add the following after the `ConnectorAudit` class (after line 242, before the relationship assignments):

**Note:** The existing codebase uses SQLAlchemy 1.x `Column()` style throughout. Follow that pattern.

```python
class ListingDetailORM(Base):
    """Raw data from detail page fetches — one row per listing observation."""

    __tablename__ = "listing_detail"

    detail_id = Column(BigInteger, primary_key=True, autoincrement=True)
    obs_id = Column(
        BigInteger, ForeignKey("listing_observation.obs_id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    description = Column(Text)
    description_length = Column(Integer)
    photo_urls = Column(ARRAY(Text))
    photo_count = Column(Integer)
    local_pickup_only = Column(Boolean)
    negotiation_enabled = Column(Boolean)
    original_posted_at = Column(TIMESTAMP(timezone=True))
    seller_account_age_days = Column(Integer)
    seller_transaction_count = Column(Integer)
    view_count = Column(Integer)
    favorite_count = Column(Integer)
    fetched_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    observation = relationship("ListingObservation", back_populates="detail")


class ListingEnrichment(Base):
    """LLM-derived enrichment analysis — one row per analyzed listing."""

    __tablename__ = "listing_enrichment"

    enrichment_id = Column(BigInteger, primary_key=True, autoincrement=True)
    obs_id = Column(
        BigInteger, ForeignKey("listing_observation.obs_id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    urgency_score = Column(Numeric(3, 2))
    urgency_keywords = Column(ARRAY(Text))
    has_original_box = Column(Boolean)
    has_receipt_or_invoice = Column(Boolean)
    accessories_included = Column(ARRAY(Text))
    accessories_completeness = Column(Numeric(3, 2))
    photo_quality_score = Column(Numeric(3, 2))
    listing_quality_score = Column(Numeric(3, 2))
    condition_confidence = Column(Numeric(3, 2))
    fakeness_probability = Column(Numeric(3, 2))
    seller_motivation_score = Column(Numeric(3, 2))
    llm_model = Column(Text)
    llm_raw_response = Column(JSONB)
    enriched_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    cost_tokens = Column(Integer)

    observation = relationship("ListingObservation", back_populates="enrichment")


class ListingScore(Base):
    """Materialized composite action scores — one row per scored listing."""

    __tablename__ = "listing_score"

    score_id = Column(BigInteger, primary_key=True, autoincrement=True)
    obs_id = Column(
        BigInteger, ForeignKey("listing_observation.obs_id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    product_id = Column(UUID, ForeignKey("product_template.product_id"), nullable=False)
    arbitrage_spread_eur = Column(Numeric)
    net_roi_pct = Column(Numeric)
    risk_adjusted_confidence = Column(Numeric(5, 2))
    acquisition_cost_eur = Column(Numeric)
    estimated_sale_price_eur = Column(Numeric)
    estimated_sell_fees_eur = Column(Numeric)
    estimated_sell_shipping_eur = Column(Numeric)
    days_on_market = Column(Integer)
    score_breakdown = Column(JSONB)
    scored_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    observation = relationship("ListingObservation", back_populates="score")
    product = relationship("ProductTemplate")
```

Also add back-populates on `ListingObservation` class (around line 89, add after existing `product` relationship):

```python
    detail = relationship("ListingDetailORM", back_populates="observation", uselist=False)
    enrichment = relationship("ListingEnrichment", back_populates="observation", uselist=False)
    score = relationship("ListingScore", back_populates="observation", uselist=False)
```

Add the Pydantic dataclass after the existing `Listing` class (~line 269):

```python
class ListingDetail(BaseModel):
    """Detail data returned by connector fetch_detail() calls."""

    obs_id: int
    description: str | None = None
    photo_urls: list[str] = []
    photo_count: int | None = None  # Computed: len(photo_urls)
    local_pickup_only: bool | None = None
    negotiation_enabled: bool | None = None
    original_posted_at: datetime | None = None
    seller_account_age_days: int | None = None
    seller_transaction_count: int | None = None
    view_count: int | None = None
    favorite_count: int | None = None

    @model_validator(mode="after")
    def set_photo_count(self) -> "ListingDetail":
        if self.photo_count is None and self.photo_urls:
            self.photo_count = len(self.photo_urls)
        return self
```

Ensure necessary imports are at the top of `models.py`:

```python
from decimal import Decimal
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as UUID_PG
```

- [ ] **Step 2: Verify models load without errors**

Run: `uv run python -c "from libs.common.models import ListingDetailORM, ListingEnrichment, ListingScore, ListingDetail; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add libs/common/models.py
git commit -m "feat(models): add ORM models for listing_detail, listing_enrichment, listing_score"
```

---

### Task 4: Enrichment Settings

**Files:**
- Modify: `libs/common/settings.py` (~line 54, after existing audit settings)

- [ ] **Step 1: Add enrichment settings**

Add after the existing audit settings block (~line 54):

```python
    # Enrichment pipeline
    enrichment_enabled: bool = True
    enrichment_batch_size: int = 50
    enrichment_re_enrichment_batch_size: int = 20
    enrichment_re_enrichment_age_days: int = 7
    enrichment_llm_model: str = "gemini-2.0-flash"
    enrichment_max_tokens_per_day: int = 500_000
    enrichment_budget_cap_eur_per_month: float = 120.0

    # Detail fetch
    detail_fetch_enabled: bool = True
    detail_fetch_pmn_threshold: float = 1.1  # fetch if price < PMN * this
    detail_fetch_rate_limit_ebay: float = 0.5  # seconds between fetches
    detail_fetch_rate_limit_lbc: float = 1.0
    detail_fetch_rate_limit_vinted: float = 2.0

    # Scoring
    scoring_confidence_threshold: float = 80.0  # min confidence for dashboard
    scoring_sell_shipping_electronics: float = 8.0
    scoring_sell_shipping_watches: float = 6.0
    scoring_sell_shipping_clothing: float = 5.0
    scoring_sell_shipping_default: float = 7.0
    scoring_vinted_buyer_fee_pct: float = 0.05
```

- [ ] **Step 2: Verify settings load**

Run: `uv run python -c "from libs.common.settings import settings; print(settings.enrichment_batch_size)"`
Expected: `50`

- [ ] **Step 3: Commit**

```bash
git add libs/common/settings.py
git commit -m "feat(settings): add enrichment pipeline and scoring configuration"
```

---

### Task 5: eBay Connector — `fetch_detail()`

**Files:**
- Modify: `ingestion/connectors/ebay.py` (add after `parse_ebay_response()`, ~line 323)
- Create: `tests/smoke/test_06_detail_fetch.py` (eBay section)

- [ ] **Step 1: Write failing smoke test for eBay detail fetch**

```python
# tests/smoke/test_06_detail_fetch.py
"""Smoke tests: detail fetch per connector (real API calls)."""
import pytest
from datetime import datetime, timezone


class TestEbayDetailFetch:
    """Test eBay detail fetch against live API."""

    @pytest.fixture
    def ebay_listings(self):
        """Fetch a few real eBay listings to get listing IDs."""
        from ingestion.connectors.ebay import fetch_ebay_listings

        listings = fetch_ebay_listings("iPhone 15", limit=3)
        assert len(listings) > 0, "eBay returned no listings"
        return listings

    def test_fetch_detail_returns_listing_detail(self, ebay_listings):
        from ingestion.connectors.ebay import fetch_detail

        listing = ebay_listings[0]
        detail = fetch_detail(listing.listing_id, obs_id=1)

        assert detail is not None
        assert detail.obs_id == 1

    def test_fetch_detail_has_description(self, ebay_listings):
        from ingestion.connectors.ebay import fetch_detail

        listing = ebay_listings[0]
        detail = fetch_detail(listing.listing_id, obs_id=1)

        assert detail is not None
        assert detail.description is not None
        assert len(detail.description) > 0

    def test_fetch_detail_has_photos(self, ebay_listings):
        from ingestion.connectors.ebay import fetch_detail

        listing = ebay_listings[0]
        detail = fetch_detail(listing.listing_id, obs_id=1)

        assert detail is not None
        assert len(detail.photo_urls) > 0
        assert detail.photo_count is not None
        assert detail.photo_count > 0
        assert detail.photo_count == len(detail.photo_urls)

    def test_fetch_detail_has_temporal_data(self, ebay_listings):
        from ingestion.connectors.ebay import fetch_detail

        listing = ebay_listings[0]
        detail = fetch_detail(listing.listing_id, obs_id=1)

        assert detail is not None
        assert detail.original_posted_at is not None
        assert detail.original_posted_at < datetime.now(timezone.utc)

    def test_fetch_detail_has_engagement_data(self, ebay_listings):
        from ingestion.connectors.ebay import fetch_detail

        listing = ebay_listings[0]
        detail = fetch_detail(listing.listing_id, obs_id=1)

        assert detail is not None
        # eBay provides view and watch counts
        assert detail.view_count is not None
        assert detail.view_count >= 0
        assert detail.favorite_count is not None
        assert detail.favorite_count >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/smoke/test_06_detail_fetch.py::TestEbayDetailFetch -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_detail' from 'ingestion.connectors.ebay'`

- [ ] **Step 3: Implement eBay `fetch_detail()`**

Add to `ingestion/connectors/ebay.py` after `parse_ebay_response()`:

```python
def fetch_detail(listing_id: str, obs_id: int) -> ListingDetail | None:
    """Fetch detailed data for a single eBay listing using the Browse API.

    Uses GetSingleItem (Shopping API) for item details including description,
    photos, seller info, and engagement metrics.
    """
    from libs.common.models import ListingDetail
    from libs.common.settings import settings

    app_id = settings.ebay_app_id
    if not app_id:
        logger.warning("EBAY_APP_ID not set, skipping detail fetch")
        return None

    url = "https://open.api.ebay.com/shopping"
    params = {
        "callname": "GetSingleItem",
        "responseencoding": "JSON",
        "appid": app_id,
        "siteid": "0",
        "version": "967",
        "ItemID": listing_id,
        "IncludeSelector": "Description,Details,ItemSpecifics,ShippingCosts",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("eBay GetSingleItem failed for %s", listing_id)
        return None

    item = data.get("Item")
    if not item:
        logger.warning("No item data for eBay listing %s", listing_id)
        return None

    # Photos
    pictures = item.get("PictureURL", [])
    if isinstance(pictures, str):
        pictures = [pictures]

    # Description (HTML)
    description = item.get("Description", "")

    # Temporal
    start_time = item.get("StartTime")
    original_posted_at = None
    if start_time:
        try:
            original_posted_at = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    # Seller
    seller = item.get("Seller", {})
    feedback_score = seller.get("FeedbackScore")
    seller_transaction_count = int(feedback_score) if feedback_score is not None else None

    # Registration date for account age
    registration_date = seller.get("RegistrationDate")
    seller_account_age_days = None
    if registration_date:
        try:
            reg_dt = datetime.fromisoformat(registration_date.replace("Z", "+00:00"))
            seller_account_age_days = (datetime.now(timezone.utc) - reg_dt).days
        except (ValueError, AttributeError):
            pass

    # Engagement
    hit_count = item.get("HitCount")
    view_count = int(hit_count) if hit_count is not None else None
    watch_count = item.get("WatchCount")
    favorite_count = int(watch_count) if watch_count is not None else None

    # Negotiation
    best_offer = item.get("BestOfferEnabled", False)
    negotiation_enabled = bool(best_offer)

    # Shipping
    shipping_type = item.get("ShippingType", "")
    local_pickup_only = shipping_type.lower() in ("pickuponly", "freepickup")

    return ListingDetail(
        obs_id=obs_id,
        description=description if description else None,
        photo_urls=pictures,
        local_pickup_only=local_pickup_only,
        negotiation_enabled=negotiation_enabled,
        original_posted_at=original_posted_at,
        seller_account_age_days=seller_account_age_days,
        seller_transaction_count=seller_transaction_count,
        view_count=view_count,
        favorite_count=favorite_count,
    )
```

Also add necessary imports at the top of the file:

```python
from libs.common.models import ListingDetail
from datetime import timezone
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/smoke/test_06_detail_fetch.py::TestEbayDetailFetch -v`
Expected: All PASS (requires valid EBAY_APP_ID in .env)

- [ ] **Step 5: Commit**

```bash
git add ingestion/connectors/ebay.py tests/smoke/test_06_detail_fetch.py
git commit -m "feat(ebay): add fetch_detail() for listing detail data"
```

---

### Task 6: LeBonCoin Connector — `fetch_detail()`

**Files:**
- Modify: `ingestion/connectors/leboncoin_api.py` (add method to `LeBonCoinAPIConnector`)
- Modify: `tests/smoke/test_06_detail_fetch.py` (add LBC section)

- [ ] **Step 1: Write failing smoke test for LBC detail fetch**

Add to `tests/smoke/test_06_detail_fetch.py`:

```python
class TestLeboncoinDetailFetch:
    """Test LeBonCoin detail fetch against live API."""

    @pytest.fixture
    def lbc_listings(self):
        from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector

        connector = LeBonCoinAPIConnector()
        listings = connector.search_items(keyword="iPhone 15", limit=3)
        assert len(listings) > 0, "LeBonCoin returned no listings"
        return listings

    def test_fetch_detail_returns_listing_detail(self, lbc_listings):
        from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector

        connector = LeBonCoinAPIConnector()
        listing = lbc_listings[0]
        detail = connector.fetch_detail(listing.listing_id, obs_id=1)

        assert detail is not None
        assert detail.obs_id == 1

    def test_fetch_detail_has_description(self, lbc_listings):
        from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector

        connector = LeBonCoinAPIConnector()
        listing = lbc_listings[0]
        detail = connector.fetch_detail(listing.listing_id, obs_id=1)

        assert detail is not None
        assert detail.description is not None
        assert len(detail.description) > 0

    def test_fetch_detail_has_photos(self, lbc_listings):
        from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector

        connector = LeBonCoinAPIConnector()
        listing = lbc_listings[0]
        detail = connector.fetch_detail(listing.listing_id, obs_id=1)

        assert detail is not None
        assert len(detail.photo_urls) > 0
        assert detail.photo_count == len(detail.photo_urls)

    def test_fetch_detail_has_original_posted_at(self, lbc_listings):
        from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector

        connector = LeBonCoinAPIConnector()
        listing = lbc_listings[0]
        detail = connector.fetch_detail(listing.listing_id, obs_id=1)

        assert detail is not None
        assert detail.original_posted_at is not None
        assert detail.original_posted_at < datetime.now(timezone.utc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/smoke/test_06_detail_fetch.py::TestLeboncoinDetailFetch -v`
Expected: FAIL — `AttributeError: 'LeBonCoinAPIConnector' object has no attribute 'fetch_detail'`

- [ ] **Step 3: Implement LBC `fetch_detail()`**

Add to `LeBonCoinAPIConnector` class in `leboncoin_api.py`. The LBC API already returns description and photo data in search results, but the `fetch_detail()` method should fetch the individual ad for richer data:

```python
def fetch_detail(self, listing_id: str, obs_id: int) -> ListingDetail | None:
    """Fetch detailed data for a single LeBonCoin listing."""
    from libs.common.models import ListingDetail

    try:
        ad = self._client.get_ad(listing_id)
    except Exception:
        logger.exception("LBC get_ad failed for %s", listing_id)
        return None

    if not ad:
        return None

    # Description
    description = (
        ad.get("body") or ad.get("description") or ad.get("content")
    )

    # Photos
    images = ad.get("images", {})
    photo_urls = []
    if isinstance(images, dict):
        urls = images.get("urls", []) or images.get("urls_large", [])
        photo_urls = [u for u in urls if isinstance(u, str)]
    elif isinstance(images, list):
        photo_urls = [img.get("url", "") for img in images if isinstance(img, dict)]

    # Original posted date
    original_posted_at = None
    for date_key in ("first_publication_date", "publication_date", "created_at"):
        raw_date = ad.get(date_key)
        if raw_date:
            try:
                original_posted_at = datetime.fromisoformat(
                    str(raw_date).replace("Z", "+00:00")
                )
                break
            except (ValueError, AttributeError):
                continue

    # Shipping / local pickup
    shipping = ad.get("shipping", {}) or {}
    has_shipping = shipping.get("is_shippable", False) or shipping.get("enabled", False)
    local_pickup_only = not has_shipping

    # Negotiation
    negotiation_enabled = ad.get("has_phone", False) or ad.get(
        "negotiation", {}
    ).get("enabled", False)

    return ListingDetail(
        obs_id=obs_id,
        description=description,
        photo_urls=photo_urls,
        local_pickup_only=local_pickup_only,
        negotiation_enabled=negotiation_enabled,
        original_posted_at=original_posted_at,
        seller_account_age_days=None,  # LBC API doesn't expose this reliably
        seller_transaction_count=None,
        view_count=None,  # LBC doesn't expose view counts
        favorite_count=None,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/smoke/test_06_detail_fetch.py::TestLeboncoinDetailFetch -v`
Expected: All PASS

- [ ] **Step 5: Commit**

- [ ] **Step 6: Add delegation in scraping connector fallback**

Add `fetch_detail()` to `LeBonCoinConnector` in `ingestion/connectors/leboncoin.py` that delegates to the API connector (the scraping connector is a fallback; detail fetch always uses the API):

```python
def fetch_detail(self, listing_id: str, obs_id: int) -> ListingDetail | None:
    """Delegate to API connector for detail fetch."""
    from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector

    api = LeBonCoinAPIConnector()
    return api.fetch_detail(listing_id, obs_id)
```

- [ ] **Step 7: Commit**

```bash
git add ingestion/connectors/leboncoin_api.py ingestion/connectors/leboncoin.py tests/smoke/test_06_detail_fetch.py
git commit -m "feat(lbc): add fetch_detail() for listing detail data"
```

---

### Task 7: Vinted Connector — `fetch_detail()`

**Files:**
- Modify: `ingestion/connectors/vinted_api.py` (add `fetch_detail()`)
- Modify: `tests/smoke/test_06_detail_fetch.py` (add Vinted section)

- [ ] **Step 1: Write failing smoke test for Vinted detail fetch**

Add to `tests/smoke/test_06_detail_fetch.py`:

```python
class TestVintedDetailFetch:
    """Test Vinted detail fetch against live API."""

    @pytest.fixture
    def vinted_listings(self):
        import asyncio
        from ingestion.connectors.vinted_api import VintedAPIConnector

        connector = VintedAPIConnector()
        listings = asyncio.run(connector.search_items("iPhone 15", limit=3))
        assert len(listings) > 0, "Vinted returned no listings"
        return listings

    def test_fetch_detail_returns_listing_detail(self, vinted_listings):
        import asyncio
        from ingestion.connectors.vinted_api import VintedAPIConnector

        connector = VintedAPIConnector()
        listing = vinted_listings[0]
        detail = asyncio.run(connector.fetch_detail(listing.listing_id, obs_id=1))

        assert detail is not None
        assert detail.obs_id == 1

    def test_fetch_detail_has_description(self, vinted_listings):
        import asyncio
        from ingestion.connectors.vinted_api import VintedAPIConnector

        connector = VintedAPIConnector()
        listing = vinted_listings[0]
        detail = asyncio.run(connector.fetch_detail(listing.listing_id, obs_id=1))

        assert detail is not None
        assert detail.description is not None
        assert len(detail.description) > 0

    def test_fetch_detail_has_photos(self, vinted_listings):
        import asyncio
        from ingestion.connectors.vinted_api import VintedAPIConnector

        connector = VintedAPIConnector()
        listing = vinted_listings[0]
        detail = asyncio.run(connector.fetch_detail(listing.listing_id, obs_id=1))

        assert detail is not None
        assert len(detail.photo_urls) > 0
        assert detail.photo_count == len(detail.photo_urls)

    def test_fetch_detail_has_engagement_data(self, vinted_listings):
        import asyncio
        from ingestion.connectors.vinted_api import VintedAPIConnector

        connector = VintedAPIConnector()
        listing = vinted_listings[0]
        detail = asyncio.run(connector.fetch_detail(listing.listing_id, obs_id=1))

        assert detail is not None
        # Vinted provides favourite count
        assert detail.favorite_count is not None
        assert detail.favorite_count >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/smoke/test_06_detail_fetch.py::TestVintedDetailFetch -v`
Expected: FAIL — `AttributeError: 'VintedAPIConnector' object has no attribute 'fetch_detail'`

- [ ] **Step 3: Implement Vinted `fetch_detail()`**

Add to `VintedAPIConnector` class in `vinted_api.py`. Creates its own `AsyncVintedScraper` instance (the connector doesn't persist one) and uses the `.item()` method:

```python
async def fetch_detail(self, listing_id: str, obs_id: int) -> ListingDetail | None:
    """Fetch detailed data for a single Vinted listing."""
    from libs.common.models import ListingDetail
    from vinted_scraper import AsyncVintedScraper

    try:
        async with AsyncVintedScraper(BASE_URL) as scraper:
            item = await scraper.item(int(listing_id))
    except Exception:
        logger.exception("Vinted item fetch failed for %s", listing_id)
        return None

    if not item:
        return None

    raw = item if isinstance(item, dict) else item.__dict__

    # Description
    description = raw.get("description") or raw.get("body")

    # Photos
    photos = raw.get("photos", [])
    photo_urls = []
    for p in photos:
        if isinstance(p, dict):
            photo_urls.append(p.get("url") or p.get("full_size_url", ""))
        elif isinstance(p, str):
            photo_urls.append(p)

    # Temporal
    original_posted_at = None
    for key in ("created_at_ts", "created_at"):
        val = raw.get(key)
        if val:
            try:
                if isinstance(val, (int, float)):
                    original_posted_at = datetime.fromtimestamp(val, tz=timezone.utc)
                else:
                    original_posted_at = datetime.fromisoformat(
                        str(val).replace("Z", "+00:00")
                    )
                break
            except (ValueError, TypeError, OSError):
                continue

    # Seller info
    user = raw.get("user", {}) or {}
    seller_account_age_days = None
    user_created = user.get("created_at") or user.get("created_at_ts")
    if user_created:
        try:
            if isinstance(user_created, (int, float)):
                reg_dt = datetime.fromtimestamp(user_created, tz=timezone.utc)
            else:
                reg_dt = datetime.fromisoformat(str(user_created).replace("Z", "+00:00"))
            seller_account_age_days = (datetime.now(timezone.utc) - reg_dt).days
        except (ValueError, TypeError, OSError):
            pass

    seller_transaction_count = (
        user.get("feedback_count") or user.get("positive_feedback_count")
    )
    if seller_transaction_count is not None:
        seller_transaction_count = int(seller_transaction_count)

    # Engagement
    favorite_count = raw.get("favourite_count") or raw.get("favorite_count")
    if favorite_count is not None:
        favorite_count = int(favorite_count)
    view_count = raw.get("view_count")
    if view_count is not None:
        view_count = int(view_count)

    # Negotiation (Vinted allows offers by default)
    negotiation_enabled = True

    return ListingDetail(
        obs_id=obs_id,
        description=description,
        photo_urls=[u for u in photo_urls if u],
        local_pickup_only=False,  # Vinted is always shipped
        negotiation_enabled=negotiation_enabled,
        original_posted_at=original_posted_at,
        seller_account_age_days=seller_account_age_days,
        seller_transaction_count=seller_transaction_count,
        view_count=view_count,
        favorite_count=favorite_count,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/smoke/test_06_detail_fetch.py::TestVintedDetailFetch -v`
Expected: All PASS

- [ ] **Step 5: Commit**

- [ ] **Step 6: Add delegation in scraping connector fallback**

Add `fetch_detail()` to `VintedConnector` in `ingestion/connectors/vinted.py` that delegates to the API connector:

```python
async def fetch_detail(self, listing_id: str, obs_id: int) -> ListingDetail | None:
    """Delegate to API connector for detail fetch."""
    from ingestion.connectors.vinted_api import VintedAPIConnector

    api = VintedAPIConnector()
    return await api.fetch_detail(listing_id, obs_id)
```

- [ ] **Step 7: Commit**

```bash
git add ingestion/connectors/vinted_api.py ingestion/connectors/vinted.py tests/smoke/test_06_detail_fetch.py
git commit -m "feat(vinted): add fetch_detail() for listing detail data"
```

---

### Task 8: Detail Fetch Orchestration + Candidate Selection

**Files:**
- Create: `ingestion/detail_fetch.py`
- Create: `tests/unit/test_detail_fetch.py`
- Modify: `ingestion/ingestion.py` (~line 743, call detail fetch after 1st-pass persist)

- [ ] **Step 1: Write failing tests for candidate selection logic**

```python
# tests/unit/test_detail_fetch.py
"""Tests for detail fetch candidate selection and persistence."""
import pytest
from decimal import Decimal
from unittest.mock import MagicMock
from ingestion.detail_fetch import should_fetch_detail


def test_fetch_when_price_below_pmn_threshold():
    """Listing priced below PMN * 1.1 should be fetched."""
    assert should_fetch_detail(
        price=Decimal("80"),
        pmn=Decimal("100"),
        pmn_threshold=1.1,
        price_min=None,
        price_max=None,
    ) is True


def test_skip_when_price_above_pmn_threshold():
    """Listing priced above PMN * 1.1 should be skipped."""
    assert should_fetch_detail(
        price=Decimal("120"),
        pmn=Decimal("100"),
        pmn_threshold=1.1,
        price_min=None,
        price_max=None,
    ) is False


def test_fetch_when_no_pmn_but_price_range():
    """No PMN but price_min/price_max defined — fetch (already passed price filter)."""
    assert should_fetch_detail(
        price=Decimal("80"),
        pmn=None,
        pmn_threshold=1.1,
        price_min=Decimal("50"),
        price_max=Decimal("200"),
    ) is True


def test_fetch_all_when_cold_start():
    """No PMN and no price range — cold start, fetch all."""
    assert should_fetch_detail(
        price=Decimal("999"),
        pmn=None,
        pmn_threshold=1.1,
        price_min=None,
        price_max=None,
    ) is True


def test_fetch_at_pmn_boundary():
    """Listing priced exactly at PMN * 1.1 should be fetched (inclusive)."""
    assert should_fetch_detail(
        price=Decimal("110"),
        pmn=Decimal("100"),
        pmn_threshold=1.1,
        price_min=None,
        price_max=None,
    ) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_detail_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement detail fetch orchestration**

```python
# ingestion/detail_fetch.py
"""Detail fetch orchestration: candidate selection and persistence."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from libs.common.models import (
    ListingDetail,
    ListingDetailORM,
    ListingObservation,
    MarketPriceNormal,
)
from libs.common.settings import settings

logger = logging.getLogger(__name__)

# Rate limits per platform (seconds between requests)
RATE_LIMITS: dict[str, float] = {
    "ebay": settings.detail_fetch_rate_limit_ebay,
    "leboncoin": settings.detail_fetch_rate_limit_lbc,
    "vinted": settings.detail_fetch_rate_limit_vinted,
}


def should_fetch_detail(
    price: Decimal | None,
    pmn: Decimal | None,
    pmn_threshold: float,
    price_min: Decimal | None,
    price_max: Decimal | None,
) -> bool:
    """Determine whether a listing should get a detail page fetch.

    Cold start: if no PMN and no price range, fetch all (bootstrap).
    """
    if price is None:
        return False

    if pmn is not None:
        return float(price) <= float(pmn) * pmn_threshold

    # No PMN — cold start
    if price_min is not None or price_max is not None:
        return True  # Already passed price filter

    return True  # Full cold start — fetch everything


def persist_listing_detail(
    db: Session,
    detail: ListingDetail,
) -> bool:
    """Upsert a ListingDetail into the listing_detail table."""
    photo_urls = detail.photo_urls or []
    values = {
        "obs_id": detail.obs_id,
        "description": detail.description,
        "description_length": len(detail.description) if detail.description else None,
        "photo_urls": photo_urls,
        "photo_count": len(photo_urls),
        "local_pickup_only": detail.local_pickup_only,
        "negotiation_enabled": detail.negotiation_enabled,
        "original_posted_at": detail.original_posted_at,
        "seller_account_age_days": detail.seller_account_age_days,
        "seller_transaction_count": detail.seller_transaction_count,
        "view_count": detail.view_count,
        "favorite_count": detail.favorite_count,
        "fetched_at": datetime.now(timezone.utc),
    }

    stmt = insert(ListingDetailORM).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["obs_id"],
        set_={k: v for k, v in values.items() if k != "obs_id"},
    )

    try:
        db.execute(stmt)
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("Failed to persist detail for obs_id=%s", detail.obs_id)
        return False


async def fetch_and_persist_details(
    db: Session,
    observations: list[ListingObservation],
    source: str,
    pmn: Decimal | None,
    price_min: Decimal | None,
    price_max: Decimal | None,
    fetch_detail_fn,
) -> int:
    """Fetch details for candidate observations and persist them.

    Returns the number of successfully persisted details.
    """
    if not settings.detail_fetch_enabled:
        return 0

    threshold = settings.detail_fetch_pmn_threshold
    rate_limit = RATE_LIMITS.get(source, 1.0)
    persisted = 0

    for obs in observations:
        if not should_fetch_detail(obs.price, pmn, threshold, price_min, price_max):
            continue

        # Rate limiting
        time.sleep(rate_limit)

        try:
            if asyncio.iscoroutinefunction(fetch_detail_fn):
                detail = await fetch_detail_fn(obs.listing_id, obs_id=obs.obs_id)
            else:
                detail = fetch_detail_fn(obs.listing_id, obs_id=obs.obs_id)
        except Exception:
            logger.exception("Detail fetch failed for %s/%s", source, obs.listing_id)
            continue

        if detail and persist_listing_detail(db, detail):
            persisted += 1

    logger.info(
        "Detail fetch for %s: %d/%d persisted",
        source,
        persisted,
        len(observations),
    )
    return persisted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_detail_fetch.py -v`
Expected: All PASS

- [ ] **Step 5: Integrate detail fetch into ingestion pipeline**

Modify `ingestion/ingestion.py` — in the main ingestion functions (e.g., `ingest_ebay_listings`, `ingest_leboncoin_listings`, `ingest_vinted_listings`), add a call to `fetch_and_persist_details()` after the `_persist_listings()` call. The exact insertion point depends on each function, but follows this pattern:

After the `_persist_listings()` call and before `update_product_metrics()`, add:

```python
# 2nd-pass: selective detail fetch
from ingestion.detail_fetch import fetch_and_persist_details

pmn_row = db.query(MarketPriceNormal).filter_by(product_id=snapshot.product_id).first()
pmn_value = pmn_row.pmn if pmn_row else None

persisted_obs = db.query(ListingObservation).filter(
    ListingObservation.product_id == snapshot.product_id,
    ListingObservation.source == source,
    ListingObservation.is_stale == False,
).all()

await fetch_and_persist_details(
    db=db,
    observations=persisted_obs,
    source=source,
    pmn=pmn_value,
    price_min=snapshot.price_min,
    price_max=snapshot.price_max,
    fetch_detail_fn=connector_fetch_detail_fn,
)
```

**Note:** Each ingestion function needs the correct `fetch_detail_fn` reference:
- eBay: `from ingestion.connectors.ebay import fetch_detail`
- LBC: `connector.fetch_detail` (method on `LeBonCoinAPIConnector`)
- Vinted: `connector.fetch_detail` (method on `VintedAPIConnector`)

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add ingestion/detail_fetch.py tests/unit/test_detail_fetch.py ingestion/ingestion.py
git commit -m "feat(ingestion): add selective detail fetch 2nd-pass with candidate selection"
```

---

## Phase 2: Enrichment Pipeline

### Task 9: LLM Enrichment Prompt + Response Parsing

**Files:**
- Create: `ingestion/enrichment_prompt.py`
- Create: `tests/unit/test_enrichment_prompt.py`

- [ ] **Step 1: Write failing tests for prompt building and response parsing**

```python
# tests/unit/test_enrichment_prompt.py
"""Tests for enrichment prompt building and response parsing."""
import pytest
import json
from ingestion.enrichment_prompt import (
    build_enrichment_prompt,
    parse_enrichment_response,
    EXPECTED_ACCESSORIES,
)


class TestBuildPrompt:
    def test_prompt_includes_listing_data(self):
        prompt = build_enrichment_prompt(
            title="iPhone 15 Pro 256GB",
            description="Selling my iPhone, cause déménagement. Boîte d'origine incluse.",
            condition_raw="Très bon état",
            price=650.0,
            currency="EUR",
            category="electronics",
            brand="Apple",
            pmn=800.0,
            photo_urls=["https://example.com/photo1.jpg"],
            days_since_posted=12,
        )
        assert "iPhone 15 Pro 256GB" in prompt
        assert "déménagement" in prompt
        assert "650" in prompt

    def test_prompt_includes_category_accessories(self):
        prompt = build_enrichment_prompt(
            title="iPhone 15",
            description="Complete set",
            condition_raw="new",
            price=700.0,
            currency="EUR",
            category="electronics",
            brand="Apple",
            pmn=800.0,
            photo_urls=[],
            days_since_posted=1,
        )
        # Should include expected accessories for electronics
        assert "charger" in prompt.lower() or "cable" in prompt.lower()

    def test_prompt_handles_no_pmn(self):
        prompt = build_enrichment_prompt(
            title="Test Item",
            description="For sale",
            condition_raw="good",
            price=100.0,
            currency="EUR",
            category="other",
            brand=None,
            pmn=None,
            photo_urls=[],
            days_since_posted=5,
        )
        assert "PMN: not available" in prompt or "no market reference" in prompt.lower()


class TestParseResponse:
    def test_parse_valid_response(self):
        raw = json.dumps({
            "urgency_score": 0.85,
            "urgency_keywords": ["déménagement"],
            "has_original_box": True,
            "has_receipt_or_invoice": False,
            "accessories_included": ["charger", "cable"],
            "accessories_completeness": 0.67,
            "photo_quality_score": 0.4,
            "listing_quality_score": 0.45,
            "condition_confidence": 0.8,
            "fakeness_probability": 0.1,
            "seller_motivation_score": 0.75,
        })
        result = parse_enrichment_response(raw)
        assert result is not None
        assert result["urgency_score"] == 0.85
        assert result["has_original_box"] is True
        assert result["accessories_included"] == ["charger", "cable"]

    def test_parse_clamps_scores(self):
        """Scores outside [0, 1] should be clamped."""
        raw = json.dumps({
            "urgency_score": 1.5,
            "urgency_keywords": [],
            "has_original_box": False,
            "has_receipt_or_invoice": False,
            "accessories_included": [],
            "accessories_completeness": -0.1,
            "photo_quality_score": 0.5,
            "listing_quality_score": 0.5,
            "condition_confidence": 0.5,
            "fakeness_probability": 2.0,
            "seller_motivation_score": 0.5,
        })
        result = parse_enrichment_response(raw)
        assert result["urgency_score"] == 1.0
        assert result["accessories_completeness"] == 0.0
        assert result["fakeness_probability"] == 1.0

    def test_parse_invalid_json(self):
        result = parse_enrichment_response("not json {{{")
        assert result is None

    def test_parse_missing_keys_returns_none(self):
        raw = json.dumps({"urgency_score": 0.5})
        result = parse_enrichment_response(raw)
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_enrichment_prompt.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement enrichment prompt and response parsing**

```python
# ingestion/enrichment_prompt.py
"""Structured LLM prompt for listing enrichment and response parsing."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Category-specific expected accessories (hardcoded hints for LLM)
EXPECTED_ACCESSORIES: dict[str, list[str]] = {
    "electronics": ["charger", "cable", "earbuds", "documentation", "SIM tool"],
    "watches": ["box", "papers", "extra links", "warranty card"],
    "clothing": [],
    "gaming": ["controller", "cables", "power supply", "documentation"],
}

ENRICHMENT_SYSTEM_PROMPT = """You are an expert marketplace listing analyst for a resale arbitrage system.
Analyze the listing and return a JSON object with your assessment.
Be precise — your scores directly affect buy/no-buy decisions on real money."""

SCORE_KEYS = [
    "urgency_score",
    "accessories_completeness",
    "photo_quality_score",
    "listing_quality_score",
    "condition_confidence",
    "fakeness_probability",
    "seller_motivation_score",
]

ALL_REQUIRED_KEYS = [
    "urgency_score",
    "urgency_keywords",
    "has_original_box",
    "has_receipt_or_invoice",
    "accessories_included",
    "accessories_completeness",
    "photo_quality_score",
    "listing_quality_score",
    "condition_confidence",
    "fakeness_probability",
    "seller_motivation_score",
]


def build_enrichment_prompt(
    title: str,
    description: str | None,
    condition_raw: str | None,
    price: float,
    currency: str,
    category: str | None,
    brand: str | None,
    pmn: float | None,
    photo_urls: list[str],
    days_since_posted: int | None,
) -> str:
    """Build the structured enrichment prompt for the LLM."""
    cat = (category or "other").lower()
    accessories = EXPECTED_ACCESSORIES.get(cat, [])
    accessories_hint = (
        f"Expected accessories for {cat}: {', '.join(accessories)}"
        if accessories
        else "No standard accessories expected for this category."
    )

    pmn_text = f"PMN (market normal price): €{pmn:.2f}" if pmn else "PMN: not available (no market reference yet)"
    dom_text = f"Days on market: {days_since_posted}" if days_since_posted is not None else "Days on market: unknown"
    photo_text = f"Number of photos: {len(photo_urls)}" if photo_urls else "No photos available"

    return f"""Analyze this marketplace listing and return a JSON object.

## Listing Data
- **Title:** {title}
- **Description:** {description or '(no description)'}
- **Stated condition:** {condition_raw or '(not stated)'}
- **Price:** {price} {currency}
- **Brand:** {brand or '(unknown)'}
- {pmn_text}
- {dom_text}
- {photo_text}
- {accessories_hint}

## Return this exact JSON structure:
{{
  "urgency_score": <float 0.0-1.0, how urgently is the seller trying to sell? Consider keywords like "déménagement", "urgent", "doit partir", price positioning vs PMN, and days on market>,
  "urgency_keywords": [<list of urgency-related keywords found in title/description, in original language>],
  "has_original_box": <true/false/null, inferred from description and photo count>,
  "has_receipt_or_invoice": <true/false/null, inferred from description mentioning "facture", "ticket de caisse", "receipt">,
  "accessories_included": [<list of accessories mentioned or visible>],
  "accessories_completeness": <float 0.0-1.0, fraction of expected accessories that are included. null if no accessories expected>,
  "photo_quality_score": <float 0.0-1.0, based on photo count: 0 photos=0.0, 1 blurry=0.2, 3+ clear=0.7, 5+ with details=0.9>,
  "listing_quality_score": <float 0.0-1.0, overall quality: description detail + photo quality + completeness>,
  "condition_confidence": <float 0.0-1.0, how much to trust the stated condition based on description consistency and photos>,
  "fakeness_probability": <float 0.0-1.0, risk of counterfeit based on price vs PMN, brand, description quality>,
  "seller_motivation_score": <float 0.0-1.0, composite: urgency + DOM + price positioning. High = very motivated seller>
}}

Return ONLY the JSON object, no additional text."""


def parse_enrichment_response(raw_response: str) -> dict | None:
    """Parse and validate LLM enrichment response.

    Returns validated dict or None if invalid.
    """
    try:
        # Handle markdown code blocks
        text = raw_response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse enrichment response as JSON")
        return None

    # Check all required keys
    missing = [k for k in ALL_REQUIRED_KEYS if k not in data]
    if missing:
        logger.warning("Enrichment response missing keys: %s", missing)
        return None

    # Clamp scores to [0, 1]
    for key in SCORE_KEYS:
        if data[key] is not None:
            data[key] = max(0.0, min(1.0, float(data[key])))

    # Ensure list types
    if not isinstance(data.get("urgency_keywords"), list):
        data["urgency_keywords"] = []
    if not isinstance(data.get("accessories_included"), list):
        data["accessories_included"] = []

    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_enrichment_prompt.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add ingestion/enrichment_prompt.py tests/unit/test_enrichment_prompt.py
git commit -m "feat(enrichment): add LLM prompt template and response parsing"
```

---

### Task 10: Enrichment Batch Job

**Files:**
- Create: `ingestion/enrichment.py`
- Modify: `ingestion/worker.py` (~line 770, register cron job)

- [ ] **Step 1: Implement enrichment batch job**

```python
# ingestion/enrichment.py
"""Hourly LLM enrichment batch job."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import google.generativeai as genai
from sqlalchemy import and_, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ingestion.enrichment_prompt import (
    build_enrichment_prompt,
    parse_enrichment_response,
)
from libs.common.db import SessionLocal
from libs.common.models import (
    ListingDetailORM,
    ListingEnrichment,
    ListingObservation,
    MarketPriceNormal,
    ProductTemplate,
)
from libs.common.settings import settings

logger = logging.getLogger(__name__)


def _get_unenriched_listings(db: Session, limit: int) -> list[tuple]:
    """Get listings with detail data but no enrichment, prioritized by price/PMN ratio."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.enrichment_re_enrichment_age_days)

    # Fresh enrichment candidates: have detail, no enrichment
    fresh = (
        db.query(
            ListingObservation,
            ListingDetailORM,
            MarketPriceNormal,
            ProductTemplate,
        )
        .join(ListingDetailORM, ListingDetailORM.obs_id == ListingObservation.obs_id)
        .outerjoin(ListingEnrichment, ListingEnrichment.obs_id == ListingObservation.obs_id)
        .outerjoin(
            MarketPriceNormal,
            MarketPriceNormal.product_id == ListingObservation.product_id,
        )
        .join(ProductTemplate, ProductTemplate.product_id == ListingObservation.product_id)
        .filter(
            ListingEnrichment.obs_id.is_(None),
            ListingObservation.is_stale == False,
        )
        .order_by(ListingObservation.price.asc())
        .limit(limit)
        .all()
    )

    # Re-enrichment candidates (capped separately)
    re_enrichment_limit = min(
        settings.enrichment_re_enrichment_batch_size,
        max(0, limit - len(fresh)),
    )

    stale = []
    if re_enrichment_limit > 0:
        stale = (
            db.query(
                ListingObservation,
                ListingDetailORM,
                MarketPriceNormal,
                ProductTemplate,
            )
            .join(ListingDetailORM, ListingDetailORM.obs_id == ListingObservation.obs_id)
            .join(ListingEnrichment, ListingEnrichment.obs_id == ListingObservation.obs_id)
            .outerjoin(
                MarketPriceNormal,
                MarketPriceNormal.product_id == ListingObservation.product_id,
            )
            .join(ProductTemplate, ProductTemplate.product_id == ListingObservation.product_id)
            .filter(
                ListingEnrichment.enriched_at < cutoff,
                ListingObservation.is_stale == False,
            )
            .order_by(ListingEnrichment.enriched_at.asc())
            .limit(re_enrichment_limit)
            .all()
        )

    return fresh + stale


def _enrich_single_listing(
    obs: ListingObservation,
    detail: ListingDetailORM,
    pmn_row: MarketPriceNormal | None,
    product: ProductTemplate,
    model: genai.GenerativeModel,
) -> dict | None:
    """Call LLM to enrich a single listing. Returns parsed result or None."""
    pmn_value = float(pmn_row.pmn) if pmn_row and pmn_row.pmn else None

    days_since = None
    if detail.original_posted_at:
        days_since = (datetime.now(timezone.utc) - detail.original_posted_at).days

    prompt = build_enrichment_prompt(
        title=obs.title or "",
        description=detail.description,
        condition_raw=obs.condition,
        price=float(obs.price) if obs.price else 0.0,
        currency=obs.currency or "EUR",
        category=product.category.name if product.category else None,
        brand=product.brand,
        pmn=pmn_value,
        photo_urls=detail.photo_urls or [],
        days_since_posted=days_since,
    )

    try:
        response = model.generate_content(prompt)
        raw_text = response.text
        tokens = response.usage_metadata.total_token_count if response.usage_metadata else None
    except Exception:
        logger.exception("LLM call failed for obs_id=%s", obs.obs_id)
        return None

    result = parse_enrichment_response(raw_text)
    if result:
        result["_raw_response"] = {"raw_text": raw_text, "parsed": result.copy()}
        result["_tokens"] = tokens
    return result


def _persist_enrichment(db: Session, obs_id: int, result: dict) -> bool:
    """Upsert enrichment result into listing_enrichment table."""
    values = {
        "obs_id": obs_id,
        "urgency_score": result.get("urgency_score"),
        "urgency_keywords": result.get("urgency_keywords"),
        "has_original_box": result.get("has_original_box"),
        "has_receipt_or_invoice": result.get("has_receipt_or_invoice"),
        "accessories_included": result.get("accessories_included"),
        "accessories_completeness": result.get("accessories_completeness"),
        "photo_quality_score": result.get("photo_quality_score"),
        "listing_quality_score": result.get("listing_quality_score"),
        "condition_confidence": result.get("condition_confidence"),
        "fakeness_probability": result.get("fakeness_probability"),
        "seller_motivation_score": result.get("seller_motivation_score"),
        "llm_model": settings.enrichment_llm_model,
        "llm_raw_response": result.get("_raw_response"),
        "enriched_at": datetime.now(timezone.utc),
        "cost_tokens": result.get("_tokens"),
    }

    stmt = insert(ListingEnrichment).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["obs_id"],
        set_={k: v for k, v in values.items() if k != "obs_id"},
    )

    try:
        db.execute(stmt)
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("Failed to persist enrichment for obs_id=%s", obs_id)
        return False


async def run_enrichment_batch(ctx: dict | None = None) -> dict:
    """Main enrichment batch job. Called by ARQ cron."""
    if not settings.enrichment_enabled:
        logger.info("Enrichment disabled, skipping batch")
        return {"status": "disabled"}

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.enrichment_llm_model)

    db = SessionLocal()
    try:
        candidates = _get_unenriched_listings(db, settings.enrichment_batch_size)
        logger.info("Enrichment batch: %d candidates", len(candidates))

        enriched = 0
        failed = 0
        total_tokens = 0

        for obs, detail, pmn_row, product in candidates:
            result = _enrich_single_listing(obs, detail, pmn_row, product, model)
            if result:
                if _persist_enrichment(db, obs.obs_id, result):
                    enriched += 1
                    total_tokens += result.get("_tokens", 0) or 0
                else:
                    failed += 1
            else:
                failed += 1

        logger.info(
            "Enrichment batch complete: %d enriched, %d failed, %d tokens",
            enriched,
            failed,
            total_tokens,
        )
        return {
            "status": "success",
            "enriched": enriched,
            "failed": failed,
            "total_tokens": total_tokens,
        }
    finally:
        db.close()
```

- [ ] **Step 2: Register enrichment cron in worker.py**

Add to `ingestion/worker.py`:

1. Add import at top: `from ingestion.enrichment import run_enrichment_batch`
2. Add to `functions` list (~line 744): `run_enrichment_batch`
3. Add to `cron_jobs` list (~line 770):

```python
cron(run_enrichment_batch, hour=None, minute=30),  # Every hour at :30
```

- [ ] **Step 3: Lint and commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add ingestion/enrichment.py ingestion/worker.py
git commit -m "feat(enrichment): add hourly LLM enrichment batch job"
```

---

### Task 10b: Enrichment Quality Tests (Structural + Golden Set)

**Files:**
- Create: `tests/smoke/test_07_enrichment.py`

- [ ] **Step 1: Write enrichment structural validation tests**

```python
# tests/smoke/test_07_enrichment.py
"""Smoke tests: enrichment quality (structural + golden set)."""
import pytest
from ingestion.enrichment_prompt import parse_enrichment_response, ALL_REQUIRED_KEYS


class TestEnrichmentStructural:
    """Structural validation of enrichment output."""

    def test_all_scores_in_range(self):
        """Valid response should have all scores clamped to [0, 1]."""
        import json

        valid = json.dumps({
            "urgency_score": 0.5,
            "urgency_keywords": ["test"],
            "has_original_box": True,
            "has_receipt_or_invoice": False,
            "accessories_included": ["charger"],
            "accessories_completeness": 0.5,
            "photo_quality_score": 0.5,
            "listing_quality_score": 0.5,
            "condition_confidence": 0.5,
            "fakeness_probability": 0.5,
            "seller_motivation_score": 0.5,
        })
        result = parse_enrichment_response(valid)
        assert result is not None
        for key in [
            "urgency_score", "accessories_completeness", "photo_quality_score",
            "listing_quality_score", "condition_confidence", "fakeness_probability",
            "seller_motivation_score",
        ]:
            assert 0.0 <= result[key] <= 1.0, f"{key} out of range: {result[key]}"

    def test_accessories_are_nonempty_strings(self):
        import json

        valid = json.dumps({
            "urgency_score": 0.5,
            "urgency_keywords": [],
            "has_original_box": False,
            "has_receipt_or_invoice": False,
            "accessories_included": ["charger", "cable"],
            "accessories_completeness": 0.5,
            "photo_quality_score": 0.5,
            "listing_quality_score": 0.5,
            "condition_confidence": 0.5,
            "fakeness_probability": 0.5,
            "seller_motivation_score": 0.5,
        })
        result = parse_enrichment_response(valid)
        for item in result["accessories_included"]:
            assert isinstance(item, str) and len(item) > 0

    def test_required_keys_present(self):
        """ALL_REQUIRED_KEYS matches spec Section 1.2."""
        expected = {
            "urgency_score", "urgency_keywords", "has_original_box",
            "has_receipt_or_invoice", "accessories_included",
            "accessories_completeness", "photo_quality_score",
            "listing_quality_score", "condition_confidence",
            "fakeness_probability", "seller_motivation_score",
        }
        assert set(ALL_REQUIRED_KEYS) == expected


class TestEnrichmentGoldenSet:
    """Golden set tests — requires labeled data in tests/fixtures/golden_set.json.

    To create: label ~20 real listings with expected enrichment values.
    Run: uv run pytest tests/smoke/test_07_enrichment.py::TestEnrichmentGoldenSet -v

    These tests are marked as xfail until the golden set file is created.
    """

    GOLDEN_SET_PATH = "tests/fixtures/golden_set.json"

    @pytest.fixture
    def golden_set(self):
        import json
        from pathlib import Path

        path = Path(self.GOLDEN_SET_PATH)
        if not path.exists():
            pytest.skip("Golden set not yet created — label ~20 real listings first")
        return json.loads(path.read_text())

    def test_boolean_accuracy_above_90pct(self, golden_set):
        """has_original_box and has_receipt match ground truth >= 90%."""
        from ingestion.enrichment_prompt import build_enrichment_prompt, parse_enrichment_response
        import google.generativeai as genai
        from libs.common.settings import settings

        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.enrichment_llm_model)

        correct_box = 0
        correct_receipt = 0
        total = 0

        for item in golden_set:
            prompt = build_enrichment_prompt(**item["input"])
            response = model.generate_content(prompt)
            result = parse_enrichment_response(response.text)
            if result is None:
                continue

            total += 1
            if result["has_original_box"] == item["expected"]["has_original_box"]:
                correct_box += 1
            if result["has_receipt_or_invoice"] == item["expected"]["has_receipt_or_invoice"]:
                correct_receipt += 1

        assert total > 0, "No golden set items were successfully enriched"
        assert correct_box / total >= 0.9, f"Box accuracy: {correct_box}/{total}"
        assert correct_receipt / total >= 0.9, f"Receipt accuracy: {correct_receipt}/{total}"

    def test_urgency_score_direction(self, golden_set):
        """Known-urgent items should score > 0.7, non-urgent < 0.3."""
        from ingestion.enrichment_prompt import build_enrichment_prompt, parse_enrichment_response
        import google.generativeai as genai
        from libs.common.settings import settings

        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.enrichment_llm_model)

        for item in golden_set:
            if "urgency_expected" not in item["expected"]:
                continue
            prompt = build_enrichment_prompt(**item["input"])
            response = model.generate_content(prompt)
            result = parse_enrichment_response(response.text)
            if result is None:
                continue

            if item["expected"]["urgency_expected"] == "high":
                assert result["urgency_score"] > 0.7, (
                    f"Known-urgent listing scored {result['urgency_score']}"
                )
            elif item["expected"]["urgency_expected"] == "low":
                assert result["urgency_score"] < 0.3, (
                    f"Known-non-urgent listing scored {result['urgency_score']}"
                )
```

- [ ] **Step 2: Run structural tests (golden set tests will skip until data is labeled)**

Run: `uv run pytest tests/smoke/test_07_enrichment.py::TestEnrichmentStructural -v`
Expected: All PASS

Run: `uv run pytest tests/smoke/test_07_enrichment.py::TestEnrichmentGoldenSet -v`
Expected: SKIPPED (golden set file not yet created)

- [ ] **Step 3: Commit**

```bash
git add tests/smoke/test_07_enrichment.py
git commit -m "test: add enrichment structural + golden set quality tests"
```

---

## Phase 3: Scoring Engine

### Task 11: Composite Score Computation

**Files:**
- Create: `ingestion/composite_scoring.py`
- Create: `tests/unit/test_composite_scoring.py`

- [ ] **Step 1: Write failing tests for score formulas**

```python
# tests/unit/test_composite_scoring.py
"""Tests for composite score computation (arithmetic + business logic)."""
import pytest
from decimal import Decimal
from ingestion.composite_scoring import (
    compute_acquisition_cost,
    compute_estimated_sale_price,
    compute_sell_fees,
    compute_arbitrage_spread,
    compute_net_roi,
    compute_risk_adjusted_confidence,
)


class TestAcquisitionCost:
    def test_basic_cost(self):
        cost = compute_acquisition_cost(
            price=Decimal("100"), shipping_cost=Decimal("10"),
            source="ebay", local_pickup_only=False,
        )
        # eBay: no buyer fee
        assert cost == Decimal("110")

    def test_vinted_buyer_fee(self):
        cost = compute_acquisition_cost(
            price=Decimal("100"), shipping_cost=Decimal("5"),
            source="vinted", local_pickup_only=False,
        )
        # Vinted: 5% buyer protection on price
        assert cost == Decimal("110")  # 100 + 5 + 5 (5% of 100)

    def test_local_pickup_zero_shipping(self):
        cost = compute_acquisition_cost(
            price=Decimal("100"), shipping_cost=Decimal("15"),
            source="leboncoin", local_pickup_only=True,
        )
        assert cost == Decimal("100")  # shipping ignored

    def test_null_shipping(self):
        cost = compute_acquisition_cost(
            price=Decimal("100"), shipping_cost=None,
            source="ebay", local_pickup_only=False,
        )
        assert cost == Decimal("100")

    def test_cost_never_less_than_price(self):
        cost = compute_acquisition_cost(
            price=Decimal("50"), shipping_cost=Decimal("0"),
            source="ebay", local_pickup_only=False,
        )
        assert cost >= Decimal("50")


class TestEstimatedSalePrice:
    def test_like_new_no_extras(self):
        price = compute_estimated_sale_price(
            pmn=Decimal("100"), condition_norm="like_new",
            has_box=False, has_receipt=False, full_accessories=False,
        )
        # like_new = 1.0, no extras
        assert price == Decimal("100")

    def test_new_with_all_extras(self):
        price = compute_estimated_sale_price(
            pmn=Decimal("100"), condition_norm="new",
            has_box=True, has_receipt=True, full_accessories=True,
        )
        # 100 * 1.10 * 1.05 * 1.05 * 1.05 = ~127.16
        expected = Decimal("100") * Decimal("1.10") * Decimal("1.05") * Decimal("1.05") * Decimal("1.05")
        assert abs(price - expected) < Decimal("0.01")

    def test_fair_condition(self):
        price = compute_estimated_sale_price(
            pmn=Decimal("100"), condition_norm="fair",
            has_box=False, has_receipt=False, full_accessories=False,
        )
        assert price == Decimal("75")

    def test_unknown_condition_defaults_good(self):
        price = compute_estimated_sale_price(
            pmn=Decimal("100"), condition_norm=None,
            has_box=False, has_receipt=False, full_accessories=False,
        )
        assert price == Decimal("90")  # good = 0.90

    def test_no_pmn_returns_none(self):
        price = compute_estimated_sale_price(
            pmn=None, condition_norm="good",
            has_box=False, has_receipt=False, full_accessories=False,
        )
        assert price is None


class TestSellFees:
    def test_ebay_fees(self):
        fees = compute_sell_fees(Decimal("100"), "ebay")
        # 12.9% + 3.0% = 15.9%
        assert abs(fees - Decimal("15.90")) < Decimal("0.01")

    def test_vinted_fees(self):
        fees = compute_sell_fees(Decimal("100"), "vinted")
        # 5% + 3% = 8%
        assert abs(fees - Decimal("8.00")) < Decimal("0.01")


class TestArbitrageSpread:
    def test_positive_spread(self):
        spread = compute_arbitrage_spread(
            estimated_sale_price=Decimal("200"),
            sell_fees=Decimal("32"),
            sell_shipping=Decimal("8"),
            acquisition_cost=Decimal("120"),
        )
        assert spread == Decimal("40")  # 200 - 32 - 8 - 120

    def test_negative_spread(self):
        spread = compute_arbitrage_spread(
            estimated_sale_price=Decimal("100"),
            sell_fees=Decimal("16"),
            sell_shipping=Decimal("8"),
            acquisition_cost=Decimal("120"),
        )
        assert spread == Decimal("-44")  # 100 - 16 - 8 - 120


class TestNetROI:
    def test_positive_roi(self):
        roi = compute_net_roi(spread=Decimal("50"), acquisition_cost=Decimal("100"))
        assert roi == Decimal("50")  # 50%

    def test_zero_acquisition_cost(self):
        roi = compute_net_roi(spread=Decimal("50"), acquisition_cost=Decimal("0"))
        assert roi is None


class TestRiskAdjustedConfidence:
    def test_all_perfect_signals(self):
        score = compute_risk_adjusted_confidence(
            seller_trust=1.0,
            fakeness_probability=0.0,
            condition_confidence=1.0,
            pmn_confidence=1.0,
            price_volatility_ratio=0.0,
            listing_quality=1.0,
        )
        assert score == 100.0

    def test_all_worst_signals(self):
        score = compute_risk_adjusted_confidence(
            seller_trust=0.0,
            fakeness_probability=1.0,
            condition_confidence=0.0,
            pmn_confidence=0.0,
            price_volatility_ratio=1.0,
            listing_quality=0.0,
        )
        assert score == 0.0

    def test_neutral_defaults(self):
        """All neutral (0.5) signals should give ~50."""
        score = compute_risk_adjusted_confidence(
            seller_trust=0.5,
            fakeness_probability=0.5,
            condition_confidence=0.5,
            pmn_confidence=0.5,
            price_volatility_ratio=0.5,
            listing_quality=0.5,
        )
        assert abs(score - 50.0) < 1.0

    def test_no_pmn_capped_at_40(self):
        score = compute_risk_adjusted_confidence(
            seller_trust=1.0,
            fakeness_probability=0.0,
            condition_confidence=1.0,
            pmn_confidence=None,  # No PMN
            price_volatility_ratio=0.0,
            listing_quality=1.0,
        )
        assert score <= 40.0

    def test_weights_sum_to_one(self):
        """Verify factor weights sum to 1.0."""
        from ingestion.composite_scoring import CONFIDENCE_WEIGHTS
        assert abs(sum(CONFIDENCE_WEIGHTS.values()) - 1.0) < 0.001
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_composite_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement composite scoring**

```python
# ingestion/composite_scoring.py
"""Composite action score computation for arbitrage opportunities."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from libs.common.condition import normalize_condition
from libs.common.settings import settings

logger = logging.getLogger(__name__)

# Platform fee rates (sell-side)
PLATFORM_SELL_FEES: dict[str, Decimal] = {
    "ebay": Decimal("0.159"),      # 12.9% + 3.0%
    "leboncoin": Decimal("0.08"),   # 5.0% + 3.0%
    "vinted": Decimal("0.08"),      # 5.0% + 3.0%
}

# Buyer-side fees (only Vinted charges buyer)
PLATFORM_BUYER_FEES: dict[str, Decimal] = {
    "ebay": Decimal("0"),
    "leboncoin": Decimal("0"),
    "vinted": Decimal("0.05"),  # ~5% buyer protection
}

# Condition adjustments
CONDITION_ADJUSTMENTS: dict[str | None, Decimal] = {
    "new": Decimal("1.10"),
    "like_new": Decimal("1.00"),
    "good": Decimal("0.90"),
    "fair": Decimal("0.75"),
    None: Decimal("0.90"),  # Unknown defaults to "good"
}

# Estimated sell shipping by category
SELL_SHIPPING: dict[str, float] = {
    "electronics": settings.scoring_sell_shipping_electronics,
    "watches": settings.scoring_sell_shipping_watches,
    "clothing": settings.scoring_sell_shipping_clothing,
}

# Risk-adjusted confidence factor weights (must sum to 1.0)
CONFIDENCE_WEIGHTS: dict[str, float] = {
    "seller_trust": 0.20,
    "fakeness_inverse": 0.25,
    "condition_confidence": 0.15,
    "pmn_confidence": 0.20,
    "price_volatility_inverse": 0.10,
    "listing_quality": 0.10,
}


def compute_acquisition_cost(
    price: Decimal,
    shipping_cost: Decimal | None,
    source: str,
    local_pickup_only: bool | None,
) -> Decimal:
    """Compute total cost to acquire a listing."""
    cost = price

    if not local_pickup_only and shipping_cost:
        cost += shipping_cost

    # Buyer platform fees
    buyer_fee_rate = PLATFORM_BUYER_FEES.get(source, Decimal("0"))
    cost += price * buyer_fee_rate

    return cost


def compute_estimated_sale_price(
    pmn: Decimal | None,
    condition_norm: str | None,
    has_box: bool,
    has_receipt: bool,
    full_accessories: bool,
) -> Decimal | None:
    """Compute estimated sale price from PMN adjusted for condition and completeness."""
    if pmn is None:
        return None

    adjustment = CONDITION_ADJUSTMENTS.get(condition_norm, CONDITION_ADJUSTMENTS[None])
    price = pmn * adjustment

    if has_box:
        price *= Decimal("1.05")
    if has_receipt:
        price *= Decimal("1.05")
    if full_accessories:
        price *= Decimal("1.05")

    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute_sell_fees(estimated_sale_price: Decimal, source: str) -> Decimal:
    """Compute platform sell-side fees."""
    rate = PLATFORM_SELL_FEES.get(source, Decimal("0.10"))
    return (estimated_sale_price * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_sell_shipping_estimate(category: str | None) -> Decimal:
    """Get flat shipping estimate by product category."""
    cat = (category or "").lower()
    cost = SELL_SHIPPING.get(cat, settings.scoring_sell_shipping_default)
    return Decimal(str(cost))


def compute_arbitrage_spread(
    estimated_sale_price: Decimal,
    sell_fees: Decimal,
    sell_shipping: Decimal,
    acquisition_cost: Decimal,
) -> Decimal:
    """Compute true profit: (sale - fees - shipping) - acquisition."""
    return estimated_sale_price - sell_fees - sell_shipping - acquisition_cost


def compute_net_roi(spread: Decimal, acquisition_cost: Decimal) -> Decimal | None:
    """Compute ROI percentage: spread / acquisition_cost * 100."""
    if acquisition_cost == 0:
        return None
    return ((spread / acquisition_cost) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute_risk_adjusted_confidence(
    seller_trust: float,
    fakeness_probability: float,
    condition_confidence: float,
    pmn_confidence: float | None,
    price_volatility_ratio: float,
    listing_quality: float,
) -> float:
    """Compute risk-adjusted confidence score (0-100).

    If pmn_confidence is None (no PMN), score is capped at 40.
    """
    no_pmn = pmn_confidence is None

    # Use neutral defaults for missing values
    pmn_conf = pmn_confidence if pmn_confidence is not None else 0.5
    volatility_inv = max(0.0, min(1.0, 1.0 - price_volatility_ratio))
    fakeness_inv = max(0.0, min(1.0, 1.0 - fakeness_probability))

    factors = {
        "seller_trust": max(0.0, min(1.0, seller_trust)),
        "fakeness_inverse": fakeness_inv,
        "condition_confidence": max(0.0, min(1.0, condition_confidence)),
        "pmn_confidence": max(0.0, min(1.0, pmn_conf)),
        "price_volatility_inverse": volatility_inv,
        "listing_quality": max(0.0, min(1.0, listing_quality)),
    }

    score = sum(factors[k] * CONFIDENCE_WEIGHTS[k] for k in CONFIDENCE_WEIGHTS) * 100

    if no_pmn:
        score = min(score, 40.0)

    return round(score, 2)


def compute_all_scores(
    obs: Any,
    detail: Any | None,
    enrichment: Any | None,
    pmn_row: Any | None,
    metrics: Any | None,
    product: Any,
) -> dict:
    """Compute all composite scores for a single listing.

    Returns dict ready for listing_score table insertion.
    """
    source = obs.source
    condition_norm = normalize_condition(obs.condition)
    category_name = product.category.name if product.category else None

    # Enrichment values (neutral defaults if missing)
    has_box = enrichment.has_original_box if enrichment else False
    has_receipt = enrichment.has_receipt_or_invoice if enrichment else False
    accessories_complete = (
        enrichment.accessories_completeness is not None
        and float(enrichment.accessories_completeness) >= 0.8
    ) if enrichment else False

    # Acquisition cost
    local_pickup = detail.local_pickup_only if detail else None
    acquisition_cost = compute_acquisition_cost(
        price=obs.price or Decimal("0"),
        shipping_cost=obs.shipping_cost,
        source=source,
        local_pickup_only=local_pickup,
    )

    # Estimated sale price
    pmn_value = pmn_row.pmn if pmn_row and pmn_row.pmn else None
    estimated_sale_price = compute_estimated_sale_price(
        pmn=pmn_value,
        condition_norm=condition_norm,
        has_box=has_box,
        has_receipt=has_receipt,
        full_accessories=accessories_complete,
    )

    # Sell fees + shipping
    sell_fees = None
    sell_shipping = get_sell_shipping_estimate(category_name)
    spread = None
    roi = None

    if estimated_sale_price:
        sell_fees = compute_sell_fees(estimated_sale_price, source)
        spread = compute_arbitrage_spread(
            estimated_sale_price, sell_fees, sell_shipping, acquisition_cost,
        )
        roi = compute_net_roi(spread, acquisition_cost)

    # Days on market
    dom = None
    if detail and detail.original_posted_at:
        dom = (datetime.now(timezone.utc) - detail.original_posted_at).days
    elif obs.observed_at:
        dom = (datetime.now(timezone.utc) - obs.observed_at).days

    # Risk-adjusted confidence
    seller_trust = 0.5  # default
    if obs.seller_rating is not None:
        seller_trust = min(float(obs.seller_rating) / 5.0, 1.0)
    if detail and detail.seller_transaction_count is not None:
        tx_signal = min(float(detail.seller_transaction_count) / 100.0, 1.0)
        seller_trust = (seller_trust + tx_signal) / 2.0
    if detail and detail.seller_account_age_days is not None:
        age_signal = min(float(detail.seller_account_age_days) / 365.0, 1.0)
        seller_trust = (seller_trust * 2 + age_signal) / 3.0

    fakeness_prob = float(enrichment.fakeness_probability) if enrichment and enrichment.fakeness_probability is not None else 0.5
    cond_conf = float(enrichment.condition_confidence) if enrichment and enrichment.condition_confidence is not None else 0.5
    pmn_conf = float(pmn_row.confidence) if pmn_row and pmn_row.confidence is not None else None
    volatility_ratio = 0.5  # default
    if metrics and metrics.price_std and pmn_value:
        volatility_ratio = min(float(metrics.price_std) / float(pmn_value), 1.0)
    listing_qual = float(enrichment.listing_quality_score) if enrichment and enrichment.listing_quality_score is not None else 0.5

    confidence = compute_risk_adjusted_confidence(
        seller_trust=seller_trust,
        fakeness_probability=fakeness_prob,
        condition_confidence=cond_conf,
        pmn_confidence=pmn_conf,
        price_volatility_ratio=volatility_ratio,
        listing_quality=listing_qual,
    )

    # Score breakdown for transparency
    breakdown = {
        "acquisition_cost": {
            "price": str(obs.price),
            "shipping": str(obs.shipping_cost),
            "buyer_fee": str(PLATFORM_BUYER_FEES.get(source, 0)),
            "local_pickup": local_pickup,
        },
        "sale_estimate": {
            "pmn": str(pmn_value) if pmn_value else None,
            "condition": condition_norm,
            "condition_adj": str(CONDITION_ADJUSTMENTS.get(condition_norm, CONDITION_ADJUSTMENTS[None])),
            "has_box": has_box,
            "has_receipt": has_receipt,
            "full_accessories": accessories_complete,
        },
        "confidence_factors": {
            "seller_trust": round(seller_trust, 3),
            "fakeness_inverse": round(1.0 - fakeness_prob, 3),
            "condition_confidence": round(cond_conf, 3),
            "pmn_confidence": round(pmn_conf, 3) if pmn_conf is not None else None,
            "price_volatility_inverse": round(1.0 - volatility_ratio, 3),
            "listing_quality": round(listing_qual, 3),
        },
    }

    return {
        "obs_id": obs.obs_id,
        "product_id": obs.product_id,
        "arbitrage_spread_eur": spread,
        "net_roi_pct": roi,
        "risk_adjusted_confidence": Decimal(str(confidence)),
        "acquisition_cost_eur": acquisition_cost,
        "estimated_sale_price_eur": estimated_sale_price,
        "estimated_sell_fees_eur": sell_fees,
        "estimated_sell_shipping_eur": sell_shipping,
        "days_on_market": dom,
        "score_breakdown": breakdown,
        "scored_at": datetime.now(timezone.utc),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_composite_scoring.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add ingestion/composite_scoring.py tests/unit/test_composite_scoring.py
git commit -m "feat(scoring): add composite score computation (spread, ROI, confidence)"
```

---

### Task 12: Scoring Batch Job + Worker Registration

**Files:**
- Modify: `ingestion/composite_scoring.py` (add batch runner)
- Modify: `ingestion/worker.py` (register scoring cron)

- [ ] **Step 1: Add scoring batch runner to composite_scoring.py**

Add at end of `ingestion/composite_scoring.py`:

```python
async def run_scoring_batch(ctx: dict | None = None) -> dict:
    """Score all listings that need scoring. Called by ARQ after enrichment."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from libs.common.db import SessionLocal
    from libs.common.models import (
        ListingDetailORM,
        ListingEnrichment,
        ListingObservation,
        ListingScore,
        MarketPriceNormal,
        ProductDailyMetrics,
        ProductTemplate,
    )

    db = SessionLocal()
    try:
        # Score: newly enriched (no score yet) + enriched with stale scores
        candidates = (
            db.query(
                ListingObservation,
                ListingDetailORM,
                ListingEnrichment,
                MarketPriceNormal,
                ProductDailyMetrics,
                ProductTemplate,
            )
            .outerjoin(ListingDetailORM, ListingDetailORM.obs_id == ListingObservation.obs_id)
            .outerjoin(ListingEnrichment, ListingEnrichment.obs_id == ListingObservation.obs_id)
            .outerjoin(
                MarketPriceNormal,
                MarketPriceNormal.product_id == ListingObservation.product_id,
            )
            .outerjoin(
                ProductDailyMetrics,
                and_(
                    ProductDailyMetrics.product_id == ListingObservation.product_id,
                    ProductDailyMetrics.date == func.current_date(),
                ),
            )
            .join(ProductTemplate, ProductTemplate.product_id == ListingObservation.product_id)
            .outerjoin(ListingScore, ListingScore.obs_id == ListingObservation.obs_id)
            .filter(
                ListingObservation.is_stale == False,
                or_(
                    ListingScore.obs_id.is_(None),  # Never scored
                    ListingScore.scored_at < ListingEnrichment.enriched_at,  # Stale score
                ),
            )
            .all()
        )

        logger.info("Scoring batch: %d candidates", len(candidates))
        scored = 0

        for obs, detail, enrichment, pmn_row, metrics, product in candidates:
            scores = compute_all_scores(obs, detail, enrichment, pmn_row, metrics, product)

            stmt = pg_insert(ListingScore).values(**scores)
            stmt = stmt.on_conflict_do_update(
                index_elements=["obs_id"],
                set_={k: v for k, v in scores.items() if k != "obs_id"},
            )
            db.execute(stmt)
            scored += 1

        db.commit()
        logger.info("Scoring batch complete: %d scored", scored)
        return {"status": "success", "scored": scored}
    except Exception:
        db.rollback()
        logger.exception("Scoring batch failed")
        return {"status": "error"}
    finally:
        db.close()
```

Add missing imports at top of `ingestion/composite_scoring.py` (add to existing imports):

```python
from sqlalchemy import and_, or_, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
```

- [ ] **Step 2: Register scoring cron in worker.py**

Add to `ingestion/worker.py`:

1. Import: `from ingestion.composite_scoring import run_scoring_batch`
2. Add to `functions` list: `run_scoring_batch`
3. Add to `cron_jobs` list:

```python
cron(run_scoring_batch, hour=None, minute=45),  # Every hour at :45 (after enrichment at :30)
```

- [ ] **Step 3: Lint and commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add ingestion/composite_scoring.py ingestion/worker.py
git commit -m "feat(scoring): add scoring batch job with ARQ cron registration"
```

---

## Phase 4: Dashboard Integration + Health Monitoring

### Task 13: Health Monitoring for Enrichment Pipeline

**Files:**
- Modify: `backend/routers/health.py` (~line 268, add enrichment stats)

- [ ] **Step 1: Add enrichment freshness endpoint**

Add a new endpoint to `backend/routers/health.py`:

```python
@router.get("/health/enrichment")
async def enrichment_health(db: Session = Depends(get_db)):
    """Check enrichment pipeline freshness."""
    from datetime import timedelta
    from libs.common.models import ListingDetailORM, ListingEnrichment, ListingScore

    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    two_hours_ago = now - timedelta(hours=2)

    # Detail coverage
    total_obs_recent = db.query(func.count(ListingObservation.obs_id)).filter(
        ListingObservation.is_stale == False,
    ).scalar()
    detail_count = db.query(func.count(ListingDetailORM.detail_id)).scalar()

    # Enrichment coverage
    enrichment_count = db.query(func.count(ListingEnrichment.enrichment_id)).scalar()

    # Score coverage
    score_count = db.query(func.count(ListingScore.score_id)).scalar()

    # Latest batch times
    latest_enrichment = db.query(func.max(ListingEnrichment.enriched_at)).scalar()
    latest_score = db.query(func.max(ListingScore.scored_at)).scalar()

    return {
        "detail_coverage": {
            "total_active_observations": total_obs_recent,
            "with_detail": detail_count,
            "coverage_pct": round(detail_count / total_obs_recent * 100, 1) if total_obs_recent else 0,
        },
        "enrichment_coverage": {
            "with_detail": detail_count,
            "with_enrichment": enrichment_count,
            "coverage_pct": round(enrichment_count / detail_count * 100, 1) if detail_count else 0,
        },
        "score_coverage": {
            "with_enrichment": enrichment_count,
            "with_score": score_count,
            "coverage_pct": round(score_count / enrichment_count * 100, 1) if enrichment_count else 0,
        },
        "latest_enrichment_at": latest_enrichment.isoformat() if latest_enrichment else None,
        "latest_score_at": latest_score.isoformat() if latest_score else None,
    }
```

- [ ] **Step 2: Verify endpoint loads**

Run: `uv run python -c "from backend.routers.health import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/routers/health.py
git commit -m "feat(health): add enrichment pipeline freshness monitoring endpoint"
```

---

### Task 14: Scored Listings API Endpoint

**Files:**
- Modify: `backend/main.py` or appropriate router file

- [ ] **Step 1: Add scored listings endpoint**

Add a new endpoint (or extend existing discovery endpoint) to serve scored listings:

```python
@router.get("/products/{product_id}/scored-listings")
async def scored_listings(
    product_id: str,
    min_confidence: float = 80.0,
    sort_by: str = "spread",  # "spread", "roi", "confidence"
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get scored listings for a product, filtered by confidence threshold."""
    from libs.common.models import (
        ListingObservation, ListingDetailORM, ListingEnrichment, ListingScore,
    )

    sort_column = {
        "spread": ListingScore.arbitrage_spread_eur.desc(),
        "roi": ListingScore.net_roi_pct.desc(),
        "confidence": ListingScore.risk_adjusted_confidence.desc(),
    }.get(sort_by, ListingScore.arbitrage_spread_eur.desc())

    rows = (
        db.query(ListingObservation, ListingScore, ListingDetailORM, ListingEnrichment)
        .join(ListingScore, ListingScore.obs_id == ListingObservation.obs_id)
        .outerjoin(ListingDetailORM, ListingDetailORM.obs_id == ListingObservation.obs_id)
        .outerjoin(ListingEnrichment, ListingEnrichment.obs_id == ListingObservation.obs_id)
        .filter(
            ListingScore.product_id == product_id,
            ListingScore.risk_adjusted_confidence >= min_confidence,
            ListingObservation.is_stale == False,
        )
        .order_by(sort_column)
        .limit(limit)
        .all()
    )

    return [
        {
            "obs_id": obs.obs_id,
            "title": obs.title,
            "price": float(obs.price) if obs.price else None,
            "source": obs.source,
            "url": obs.url,
            "condition": obs.condition,
            "seller_rating": float(obs.seller_rating) if obs.seller_rating else None,
            "arbitrage_spread_eur": float(score.arbitrage_spread_eur) if score.arbitrage_spread_eur else None,
            "net_roi_pct": float(score.net_roi_pct) if score.net_roi_pct else None,
            "risk_adjusted_confidence": float(score.risk_adjusted_confidence) if score.risk_adjusted_confidence else None,
            "acquisition_cost_eur": float(score.acquisition_cost_eur) if score.acquisition_cost_eur else None,
            "estimated_sale_price_eur": float(score.estimated_sale_price_eur) if score.estimated_sale_price_eur else None,
            "days_on_market": score.days_on_market,
            "score_breakdown": score.score_breakdown,
            "scored_at": score.scored_at.isoformat() if score.scored_at else None,
            # Detail data
            "photo_count": detail.photo_count if detail else None,
            "local_pickup_only": detail.local_pickup_only if detail else None,
            "negotiation_enabled": detail.negotiation_enabled if detail else None,
            "view_count": detail.view_count if detail else None,
            "favorite_count": detail.favorite_count if detail else None,
            # Enrichment data
            "urgency_score": float(enrichment.urgency_score) if enrichment and enrichment.urgency_score else None,
            "seller_motivation_score": float(enrichment.seller_motivation_score) if enrichment and enrichment.seller_motivation_score else None,
            "has_original_box": enrichment.has_original_box if enrichment else None,
            "listing_quality_score": float(enrichment.listing_quality_score) if enrichment and enrichment.listing_quality_score else None,
        }
        for obs, score, detail, enrichment in rows
    ]
```

- [ ] **Step 2: Verify endpoint loads**

Run: `uv run python -c "from backend.main import app; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add backend/
git commit -m "feat(api): add scored listings endpoint with composite scores"
```

---

### Task 15: Final Integration Tests

**Files:**
- Create: `tests/smoke/test_08_composite_scoring.py`

- [ ] **Step 1: Write business logic smoke tests on real data**

```python
# tests/smoke/test_08_composite_scoring.py
"""Smoke tests: composite scoring business logic on real data."""
import pytest
from decimal import Decimal
from ingestion.composite_scoring import (
    compute_acquisition_cost,
    compute_estimated_sale_price,
    compute_arbitrage_spread,
    compute_sell_fees,
    compute_net_roi,
    compute_risk_adjusted_confidence,
    get_sell_shipping_estimate,
)


class TestScoringBusinessLogic:
    """Validate scoring makes economic sense with realistic numbers."""

    def test_iphone_good_deal(self):
        """iPhone 15 at €500 when PMN is €800 — should be profitable."""
        acq = compute_acquisition_cost(
            Decimal("500"), Decimal("10"), "vinted", False,
        )
        sale = compute_estimated_sale_price(
            Decimal("800"), "like_new", True, False, False,
        )
        fees = compute_sell_fees(sale, "ebay")
        shipping = get_sell_shipping_estimate("electronics")
        spread = compute_arbitrage_spread(sale, fees, shipping, acq)
        roi = compute_net_roi(spread, acq)

        assert spread > Decimal("0"), "Good deal should have positive spread"
        assert roi > Decimal("20"), "Good deal should have >20% ROI"

    def test_overpriced_listing(self):
        """Listing above PMN should have negative spread."""
        acq = compute_acquisition_cost(
            Decimal("900"), Decimal("10"), "ebay", False,
        )
        sale = compute_estimated_sale_price(
            Decimal("800"), "good", False, False, False,
        )
        fees = compute_sell_fees(sale, "ebay")
        shipping = get_sell_shipping_estimate("electronics")
        spread = compute_arbitrage_spread(sale, fees, shipping, acq)

        assert spread < Decimal("0"), "Overpriced listing should have negative spread"

    def test_high_confidence_good_signals(self):
        """High quality signals should produce confidence > 80."""
        conf = compute_risk_adjusted_confidence(
            seller_trust=0.9,
            fakeness_probability=0.05,
            condition_confidence=0.9,
            pmn_confidence=0.85,
            price_volatility_ratio=0.1,
            listing_quality=0.8,
        )
        assert conf > 80.0, f"Good signals should give confidence > 80, got {conf}"

    def test_suspicious_listing_low_confidence(self):
        """High fakeness + bad seller should produce confidence < 50."""
        conf = compute_risk_adjusted_confidence(
            seller_trust=0.2,
            fakeness_probability=0.8,
            condition_confidence=0.3,
            pmn_confidence=0.7,
            price_volatility_ratio=0.5,
            listing_quality=0.3,
        )
        assert conf < 50.0, f"Suspicious listing should give confidence < 50, got {conf}"

    def test_local_pickup_advantage(self):
        """Local pickup should reduce acquisition cost vs shipped."""
        shipped = compute_acquisition_cost(
            Decimal("100"), Decimal("15"), "leboncoin", False,
        )
        pickup = compute_acquisition_cost(
            Decimal("100"), Decimal("15"), "leboncoin", True,
        )
        assert pickup < shipped, "Local pickup should be cheaper"
        assert pickup == Decimal("100"), "Local pickup should ignore shipping"
```

- [ ] **Step 2: Run all smoke tests**

Run: `uv run pytest tests/smoke/test_08_composite_scoring.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/unit/ -v`
Expected: All PASS

- [ ] **Step 4: Lint everything**

Run: `uv run ruff check --fix . && uv run ruff format .`

- [ ] **Step 5: Final commit**

```bash
git add tests/smoke/test_08_composite_scoring.py
git commit -m "test: add composite scoring business logic smoke tests"
```

---

### Task 16: Update CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add enriched data model entry**

Add at the top of `CHANGELOG.md`:

```markdown
## [Unreleased]

### Added
- **Enriched data model** — Three new tables (`listing_detail`, `listing_enrichment`, `listing_score`) for capturing logistics, seller psychology, temporal signals, and product completeness
- **Selective detail fetch** — 2nd-pass detail page fetching for promising listings with cold-start fallback
- **LLM enrichment pipeline** — Hourly batch job analyzing listings via Gemini Flash for urgency, completeness, photo quality, fakeness, and seller motivation
- **Composite action scores** — `arbitrage_spread_eur`, `net_roi_pct`, `risk_adjusted_confidence` materialized after each enrichment batch
- **Shared condition normalization** — Extracted from 5 connector files into `libs/common/condition.py`
- **Scored listings API** — `/products/{id}/scored-listings` endpoint with sort and confidence filter
- **Enrichment health monitoring** — `/health/enrichment` endpoint tracking pipeline freshness

### Changed
- Connectors now use shared `normalize_condition()` instead of per-connector implementations
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: update CHANGELOG for enriched data model"
```
