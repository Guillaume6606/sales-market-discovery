# Market Discovery & PMN — Skeleton

Mono‑repo skeleton for a Flip/Resell Market Discovery app with **FastAPI**, **Streamlit**, **PostgreSQL**, **Redis**, **Arq**, **Alembic**, and **Advanced Web Scraping** for eBay and LeBonCoin integration.

## Components
- `backend/` — FastAPI app exposing product discovery and alert rules APIs.
- `ingestion/` — Advanced connectors (eBay API + LeBonCoin scraping), normalization, PMN engine, scheduled via Arq workers.
- `ui/` — Streamlit MVP dashboard (Discovery board, Product page, Rules).
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

### 📊 Real-Time Analytics
- **PMN Calculations**: Predicted Market Net with confidence intervals
- **Liquidity Scoring**: Based on sales volume and frequency
- **Trend Analysis**: Moving averages and price trend detection
- **Multi-Source Aggregation**: Combined insights from eBay and LeBonCoin

### Quick start (local)
```bash
# 1) Prepare env
cp .env.example .env

# 2) Start stack
docker compose up --build -d

# 3) Initialize DB
docker compose exec backend alembic upgrade head

# 4) Open apps
# FastAPI docs: http://localhost:8000/docs
# Streamlit:    http://localhost:8501
```

### API Usage

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

### Environment Variables

Configure these in your `.env` file:

```bash
# Scraping behavior
SCRAPING_MIN_DELAY=1.0      # Minimum delay between requests (seconds)
SCRAPING_MAX_DELAY=3.0      # Maximum delay between requests (seconds)
SCRAPING_TIMEOUT=30.0       # Request timeout (seconds)
SCRAPING_MAX_RETRIES=3      # Maximum retry attempts

# Playwright settings
PLAYWRIGHT_HEADLESS=true    # Run browsers in headless mode
PLAYWRIGHT_SLOW_MO=0        # Slow down actions (for debugging)

# Optional: Proxy settings
HTTP_PROXY=                 # HTTP proxy URL
HTTPS_PROXY=               # HTTPS proxy URL
```

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
