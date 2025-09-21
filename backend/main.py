from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from libs.common.db import get_db
from libs.common.log import logger

app = FastAPI(title="Market Discovery API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DiscoveryItem(BaseModel):
    product_id: str
    title: str
    brand: str | None = None
    pmn: float | None = None
    price_min_market: float | None = None
    delta_vs_pmn_pct: float | None = None
    liquidity_score: float | None = None
    trend_score: float | None = None

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/products/discovery")
def products_discovery(
    category: str | None = None,
    brand: str | None = None,
    min_margin: float | None = Query(None, description="min % below PMN"),
    limit: int = 50,
    db: Session = Depends(get_db),
):
    # Placeholder: return mock data for now
    logger.info("Discovery query: category=%s brand=%s", category, brand)
    items = [
        DiscoveryItem(
            product_id="00000000-0000-0000-0000-000000000001",
            title="Sony WH-1000XM4 Headphones",
            brand="Sony",
            pmn=180.0,
            price_min_market=140.0,
            delta_vs_pmn_pct=-22.2,
            liquidity_score=0.9,
            trend_score=1.2,
        )
    ]
    return {"items": [i.model_dump() for i in items][:limit]}

class ProductDetail(BaseModel):
    product_id: str
    title: str
    brand: str | None = None
    pmn: float | None = None
    pmn_low: float | None = None
    pmn_high: float | None = None
    recent_solds: list[dict]
    live_listings: list[dict]

@app.get("/products/{product_id}")
def product_detail(product_id: str, db: Session = Depends(get_db)):
    # Placeholder with mock data
    return ProductDetail(
        product_id=product_id,
        title="Sony WH-1000XM4 Headphones",
        brand="Sony",
        pmn=180.0,
        pmn_low=170.0,
        pmn_high=190.0,
        recent_solds=[{"price": 175, "date": "2025-09-15", "condition": "good"}],
        live_listings=[{"price": 149, "seller": "john_doe", "link": "https://example.com"}],
    ).model_dump()
