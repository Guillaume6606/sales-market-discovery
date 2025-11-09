# Jupyter Notebooks for Market Discovery

## Quick Start

### 1. Start Jupyter Lab

**Option A: Standalone Jupyter (Recommended for Experiments)**

```bash
# Start Jupyter Lab in the ingestion container
docker compose exec ingestion bash /app/infra/docker/start-jupyter.sh
```

**Option B: Run in Background**

Update the ingestion service command in `docker-compose.yml` to start Jupyter automatically, or use:

```bash
# In a separate terminal
docker compose exec -d ingestion jupyter lab \
    --ip=0.0.0.0 \
    --port=8888 \
    --no-browser \
    --NotebookApp.token='' \
    --NotebookApp.password='' \
    --NotebookApp.allow_origin='*' \
    --notebook-dir=/app/notebooks
```

### 2. Access Jupyter Lab

Open your browser to: **http://localhost:8888**

(No password required - configured for local development)

---

## Running Your Scraping Code

### Example: Cdiscount Scraping

```python
import sys
sys.path.insert(0, '/app')

from ingestion.connectors.cdiscount_connector import CdiscountConnector

# Initialize connector
connector = CdiscountConnector()

# Run search (use 'await' in notebook cells)
listings = await connector.search_items("Nintendo Switch", limit=5)

# Display results
for listing in listings:
    print(f"{listing.title}: {listing.price} {listing.currency}")
```

### Example: Deep Product Parsing

```python
# Parse detailed product page
if listings:
    detailed = await connector.parse_product_page(listings[0].url)
    print(f"Brand: {detailed.brand}")
    print(f"Description: {detailed.description[:200]}...")
```

### Example: Compare All Connectors

```python
from ingestion.connectors.fnac_connector import FnacConnector
from ingestion.connectors.backmarket_connector import BackmarketConnector
from ingestion.connectors.rakuten_connector import RakutenConnector

connectors = {
    'cdiscount': CdiscountConnector(),
    'fnac': FnacConnector(),
    'backmarket': BackmarketConnector(),
    'rakuten': RakutenConnector(),
}

results = {}
for name, conn in connectors.items():
    try:
        results[name] = await conn.search_items("iPhone 13", limit=3)
        print(f"{name}: {len(results[name])} results")
    except Exception as e:
        print(f"{name}: Error - {e}")
```

---

## Available Features

### 1. **Stealth Scraping with Playwright**

All connectors use Playwright with anti-bot detection measures:
- User agent rotation
- Browser fingerprinting
- Request delays
- Headless mode

### 2. **Data Analysis with Pandas**

```python
import pandas as pd

# Convert listings to DataFrame
df = pd.DataFrame([l.dict() for l in listings])
df[['title', 'price', 'currency', 'condition_norm']]
```

### 3. **Visualization**

```python
import matplotlib.pyplot as plt

# Price distribution
df['price'].hist(bins=20)
plt.title('Price Distribution')
plt.xlabel('Price (EUR)')
plt.show()
```

### 4. **Export Results**

```python
# Export to CSV
df.to_csv('/app/notebooks/results.csv', index=False)

# Export to JSON
import json
with open('/app/notebooks/results.json', 'w') as f:
    json.dump([l.dict() for l in listings], f, indent=2, default=str)
```

---

## LLM Experiments (Future)

Once the LLM service is implemented, you can test refinement here:

```python
# Placeholder for LLM integration
from libs.common.llm_service import LLMService

llm = LLMService(provider="openai", model="gpt-4o-mini")

# Refine a listing
assessment, metadata = await llm.assess_listing_relevance(
    listing_title=listings[0].title,
    listing_description=listings[0].description,
    listing_price=listings[0].price,
    listing_condition=listings[0].condition_raw,
    product_name="Nintendo Switch OLED",
    product_brand="Nintendo",
    product_category="Gaming Consoles",
)

print(f"Relevance: {assessment.relevance_score}")
print(f"Reasoning: {assessment.relevance_reasoning}")
print(f"Risk Flags: {assessment.risk_flags}")
```

---

## Tips & Tricks

### Debug Playwright Issues

```python
from libs.common.scraping import ScrapingSession

# Create a session with custom settings
session = ScrapingSession(
    use_playwright=True,
    headless=False,  # See browser window
    enable_stealth=True
)

# Use it for scraping
html = await session.get_html_with_playwright("https://example.com")
```

### Async/Await in Notebooks

Jupyter notebooks support top-level `await` - you don't need to wrap async calls:

```python
# This works directly in notebook cells
listings = await connector.search_items("query", limit=10)
```

### Accessing the Database

```python
from libs.common.db import SessionLocal
from libs.common.models import ProductTemplate, ListingObservation

with SessionLocal() as db:
    # Query products
    products = db.query(ProductTemplate).limit(10).all()
    
    # Query listings
    listings = db.query(ListingObservation).limit(100).all()
```

### Environment Variables

All environment variables from `.env` are available:

```python
import os
print(f"Database: {os.getenv('POSTGRES_DB')}")
print(f"Redis: {os.getenv('REDIS_URL')}")
```

---

## Troubleshooting

### "Module not found" errors

```python
import sys
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/libs')
```

### Playwright not working

Make sure you're running in the ingestion container:

```bash
docker compose exec ingestion jupyter lab ...
```

### Port 8888 already in use

Change the port mapping in `docker-compose.yml`:

```yaml
ports:
  - "8889:8888"  # Use 8889 instead
```

---

## Keyboard Shortcuts

- `Shift + Enter`: Run cell and move to next
- `Ctrl + Enter`: Run cell
- `Alt + Enter`: Run cell and insert below
- `A`: Insert cell above
- `B`: Insert cell below
- `DD`: Delete cell
- `M`: Convert to Markdown
- `Y`: Convert to Code

---

## Example Notebooks

Check the `notebooks/` directory for:
- `scraping_experiments.ipynb` - Basic scraping examples
- More coming soon!

---

Happy experimenting! 🚀


