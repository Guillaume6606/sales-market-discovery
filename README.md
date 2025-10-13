# Market Discovery & PMN — Skeleton

Mono‑repo skeleton for a Flip/Resell Market Discovery app with **FastAPI**, **Streamlit**, **PostgreSQL**, **Redis**, **Arq**, **Alembic**, and **Advanced Web Scraping** for eBay and LeBonCoin integration.

## Components
- `backend/` — FastAPI app exposing product discovery and alert rules APIs.
- `ingestion/` — Advanced connectors (eBay API + LeBonCoin scraping), normalization, PMN engine, scheduled via Arq workers.
- `ui/` — Streamlit dashboard with arbitrage discovery, product details, and price history charts.
- `libs/common/` — Shared utilities including advanced web scraping with anti-bot detection bypass.
- `infra/` — Docker & compose for local dev, Alembic migrations, pre-commit, Makefile.

## Features

### 🛒 Multi-Platform Integration
- **eBay API Integration**: Direct API access for sold items and active listings
- **LeBonCoin Web Scraping**: Advanced scraping with anti-bot detection bypass
- **Vinted Web Scraping**: Fashion marketplace scraping with brand/size/color detection
- **Unified Data Model**: Consistent data structure across all platforms

### 🛡️ Advanced Web Scraping
- **Anti-Bot Detection**: CloudScraper + Playwright for bypassing detection
- **User-Agent Rotation**: Realistic browser fingerprinting
- **Request Delays**: Configurable delays to respect rate limits
- **Retry Logic**: Intelligent backoff and error handling
- **Proxy Support**: Optional proxy configuration for enhanced anonymity
- **Stealth Testing**: Built-in CreepJS analysis for fingerprinting evaluation

### 📊 Real-Time Analytics & Discovery
- **PMN Calculations**: Predicted Market Net with confidence intervals
- **Arbitrage Discovery**: Find products with listings below market price
- **Liquidity Scoring**: Based on sales volume and frequency
- **Trend Analysis**: Moving averages and price trend detection
- **Multi-Source Aggregation**: Combined insights from eBay, LeBonCoin, and Vinted
- **Interactive Dashboard**: Streamlit UI with filters, charts, and real-time updates

### Quick start (local)
```bash
# 1) Prepare env
cp .env.example .env

# 2) Start stack
docker compose up --build -d

# 3) Initialize DB
docker compose exec backend alembic upgrade head

# 4) Add performance indexes (recommended for production use)
docker compose exec postgres psql -U market_discovery -d market_discovery -c "
CREATE INDEX IF NOT EXISTS idx_listing_product_sold ON listing_observation(product_id, is_sold);
CREATE INDEX IF NOT EXISTS idx_listing_observed_at ON listing_observation(observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_listing_price ON listing_observation(price) WHERE price IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_listing_source ON listing_observation(source);
CREATE INDEX IF NOT EXISTS idx_metrics_product_date ON product_daily_metrics(product_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_product_active ON product_template(is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_pmn_product ON market_price_normal(product_id);
"

# 5) Open apps
# FastAPI docs: http://localhost:8000/docs
# Streamlit:    http://localhost:8501
```

### API Usage

#### Discovery & Analytics
```bash
# Get arbitrage opportunities
curl "http://localhost:8000/products/discovery?sort_by=margin&limit=50"

# Filter by margin
curl "http://localhost:8000/products/discovery?min_margin=-50&max_margin=-20"

# Get product details
curl "http://localhost:8000/products/{product_id}"

# Get price history
curl "http://localhost:8000/products/{product_id}/price-history?days=30"

# Get analytics overview
curl "http://localhost:8000/analytics/overview"

# Get top opportunities
curl "http://localhost:8000/analytics/top-opportunities?limit=10"
```

#### Product Ingestion
```bash
# Trigger full ingestion for a product
curl -X POST "http://localhost:8000/ingestion/trigger?product_id={id}&sold_limit=50&listings_limit=50&sources=ebay&sources=vinted"
```

#### eBay Integration
```bash
# Trigger eBay ingestion
curl -X POST "http://localhost:8000/ingestion/trigger?keyword=iPhone"

# Trigger eBay sold items only
curl -X POST "http://localhost:8000/ingestion/trigger-sold?keyword=iPhone&limit=50"

# Trigger eBay listings only
curl -X POST "http://localhost:8000/ingestion/trigger-listings?keyword=iPhone&limit=50"
```

#### LeBonCoin Integration
```bash
# Trigger LeBonCoin ingestion
curl -X POST "http://localhost:8000/ingestion/leboncoin/trigger?keyword=iPhone"

# Trigger LeBonCoin listings only
curl -X POST "http://localhost:8000/ingestion/leboncoin/trigger-listings?keyword=iPhone&limit=50"

# Trigger LeBonCoin 'sold' items (recent listings as proxy)
curl -X POST "http://localhost:8000/ingestion/leboncoin/trigger-sold?keyword=iPhone&limit=50"
```

#### Vinted Integration
```bash
# Trigger Vinted ingestion
curl -X POST "http://localhost:8000/ingestion/vinted/trigger?keyword=Nike"

# Trigger Vinted listings only
curl -X POST "http://localhost:8000/ingestion/vinted/trigger-listings?keyword=Adidas&limit=50"

# Trigger Vinted 'sold' items (recent listings as proxy)
curl -X POST "http://localhost:8000/ingestion/vinted/trigger-sold?keyword=Zara&limit=50"
```

#### Status and Statistics
```bash
# Get ingestion status and statistics
curl http://localhost:8000/ingestion/status
```

### Automated Scheduling
- **eBay ingestion**: Daily at 2:00 AM
- **LeBonCoin ingestion**: Daily at 3:00 AM
- **Vinted ingestion**: Daily at 4:00 AM
- **Background processing**: Non-blocking ingestion via Arq workers

### Dev commands
```bash
make fmt        # format with ruff/black
make lint       # lint
make test       # run pytest
make up         # docker compose up -d
make down       # docker compose down -v
```

## Troubleshooting

### Docker Setup Issues

#### Playwright Browser Installation
If you encounter issues with Playwright browser installation in Docker:

1. **Build fails**: The ingestion container will attempt to install browsers at startup
2. **Permission issues**: Ensure Docker has sufficient permissions for browser downloads
3. **Network issues**: Playwright downloads may fail due to network restrictions

**Manual browser installation** (if needed):
```bash
# Install browsers manually
docker compose exec ingestion poetry run playwright install chromium

# Or install all browsers
docker compose exec ingestion poetry run playwright install
```

#### Scraping Dependencies
The ingestion container includes all necessary system dependencies for web scraping:
- GTK libraries for GUI rendering
- Audio libraries for browser audio
- X11 libraries for display simulation


### Common Issues

1. **Import errors**: Ensure all dependencies are installed with `poetry install`
2. **Browser not found**: Playwright browsers may need manual installation
3. **Permission denied**: Check Docker container permissions for browser downloads
4. **Rate limiting**: Adjust `SCRAPING_*_DELAY` values if hitting rate limits

### Browser Fingerprinting Stealth Testing

The system includes a comprehensive test to evaluate how discreet your browser setup is against fingerprinting detection using both CreepJS and BrowserScan:

#### Quick Test
```bash
# Run stealth test inside container
./test-stealth-container.sh
```

#### Manual Testing
```bash
# Start services
docker compose up -d

# Run test (headless mode - recommended)
docker compose exec -e RUN_STEALTH_TEST=true ingestion /start-ingestion.sh

# Alternative: Run with GUI mode (requires xvfb)
docker compose exec ingestion xvfb-run -a python /app/test-stealth.py

# New: Run with ScrapingSession integration (uses scraping.py configuration)
docker compose exec ingestion python /app/test-stealth-config.py
```

#### Test Results Interpretation
- **🟢 Excellent (0-10% detection)**: Very discreet setup
- **🟡 Good (10-30% detection)**: Moderately discreet
- **🟠 Moderate (30-60% detection)**: Some detection risk
- **🔴 Poor (60%+ detection)**: Highly detectable

**Test Coverage:**
- **CreepJS**: WebRTC, Canvas/WebGL, fonts, timezone, plugins, and comprehensive fingerprinting
- **BrowserScan**: WebDriver detection, User-Agent analysis, CDP detection, Navigator properties

**Test Scripts:**
- **`test-stealth.py`**: Original test script with direct Playwright usage
- **`test-stealth-config.py`**: New script using ScrapingSession from `scraping.py` for consistent configuration

**Analysis Areas:**
- WebRTC fingerprinting and device detection
- Canvas/WebGL rendering consistency
- Browser properties and headers
- Automation tool detection
- Timing and behavior patterns

See `/docs/ARCHITECTURE.md` for design & roadmap.
