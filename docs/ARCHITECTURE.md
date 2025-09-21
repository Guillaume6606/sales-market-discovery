# Architecture Overview

- **FastAPI** backend exposes discovery & product endpoints.
- **Ingestion** workers fetch marketplace data (eBay first), normalize it, and compute **PMN**.
- **Streamlit** UI provides discovery board and product details view.
- **PostgreSQL** stores normalized records and aggregates. **Redis** backs workers/queues.

## Data Flow
1) Connectors pull data (sold + live listings) → `listing_observation`
2) Aggregations produce daily metrics → `product_daily_metrics`
3) Pricing engine computes **PMN** → `market_price_normal`
4) API reads from these tables → UI displays opportunities

## Next steps
- Add Vinted & Leboncoin connectors
- Add alert rules engine + Telegram notifications
- Add product matching & brand/category normalization
