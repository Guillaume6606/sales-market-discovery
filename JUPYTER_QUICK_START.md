# 🚀 Jupyter Lab Quick Start Guide

## ✅ Setup Complete!

Your ingestion container now has Jupyter Lab installed with full access to:
- All scraping connectors (Cdiscount, Fnac, BackMarket, Rakuten, eBay, LeBonCoin, Vinted)
- Playwright for stealth scraping
- Database access
- All Python libraries (pandas, matplotlib, etc.)

---

## 🎯 Start Jupyter Lab

### Method 1: Quick Start (Recommended)

```bash
# Start Jupyter in the ingestion container (in background)
docker compose exec -d ingestion jupyter lab \
    --ip=0.0.0.0 \
    --port=8888 \
    --no-browser \
    --NotebookApp.token='' \
    --NotebookApp.password='' \
    --NotebookApp.allow_origin='*' \
    --notebook-dir=/app/notebooks
```

Then open: **http://localhost:8888**

### Method 2: Interactive Terminal

```bash
# Open a shell in the container
docker compose exec ingestion bash

# Start Jupyter
jupyter lab --ip=0.0.0.0 --port=8888 --no-browser \
    --NotebookApp.token='' --NotebookApp.allow_origin='*' \
    --notebook-dir=/app/notebooks
```

---

## 📝 Your First Scraping Experiment

Open Jupyter Lab at **http://localhost:8888** and create a new notebook:

### Cell 1: Setup

```python
import sys
sys.path.insert(0, '/app')

from ingestion.connectors.cdiscount_connector import CdiscountConnector
from ingestion.connectors.fnac_connector import FnacConnector
import pandas as pd

print("✅ Ready to scrape!")
```

### Cell 2: Run a Search

```python
# Initialize connector
connector = CdiscountConnector()

# Search (use await - notebooks support top-level await)
listings = await connector.search_items("Nintendo Switch", limit=5)

print(f"Found {len(listings)} listings")

# Display first result
if listings:
    first = listings[0]
    print(f"\nTitle: {first.title}")
    print(f"Price: {first.price} {first.currency}")
    print(f"Condition: {first.condition_norm}")
    print(f"URL: {first.url}")
```

### Cell 3: Analyze with Pandas

```python
# Convert to DataFrame
df = pd.DataFrame([l.dict() for l in listings])

# Show key columns
df[['title', 'price', 'currency', 'condition_norm', 'brand']]
```

### Cell 4: Deep Product Parsing

```python
# Parse detailed product page
if listings and listings[0].url:
    detailed = await connector.parse_product_page(listings[0].url)
    
    if detailed:
        print(f"Brand: {detailed.brand}")
        print(f"Description length: {len(detailed.description or '')} chars")
        print(f"\nFirst 200 chars:")
        print(detailed.description[:200] if detailed.description else "No description")
```

---

## 🎨 Advanced Examples

### Compare All Connectors

```python
connectors = {
    'cdiscount': CdiscountConnector(),
    'fnac': FnacConnector(),
}

results = {}
query = "iPhone 13"

for name, conn in connectors.items():
    try:
        listings = await conn.search_items(query, limit=3)
        results[name] = listings
        print(f"{name}: {len(listings)} results")
        
        if listings:
            prices = [l.price for l in listings if l.price]
            if prices:
                print(f"  Avg price: {sum(prices)/len(prices):.2f} EUR")
    except Exception as e:
        print(f"{name}: Error - {e}")
```

### Visualize Price Distribution

```python
import matplotlib.pyplot as plt

# Get all prices
all_prices = []
for source, items in results.items():
    for item in items:
        if item.price:
            all_prices.append(item.price)

# Plot histogram
plt.figure(figsize=(10, 6))
plt.hist(all_prices, bins=15, edgecolor='black', alpha=0.7)
plt.title(f'Price Distribution for "{query}"')
plt.xlabel('Price (EUR)')
plt.ylabel('Frequency')
plt.grid(True, alpha=0.3)
plt.show()
```

### Export Results

```python
from datetime import datetime

# Export to CSV
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
filename = f"/app/notebooks/results_{timestamp}.csv"

all_listings = [l for lists in results.values() for l in lists]
df = pd.DataFrame([l.dict() for l in all_listings])

df.to_csv(filename, index=False)
print(f"✅ Exported to: {filename}")
```

---

## 🔧 Running Your Existing Scripts

You can run your existing scripts directly from Jupyter:

```python
# Run test_connector.py equivalent
from test_connector import test_connector
import asyncio

# Test Cdiscount
await test_connector(
    connectors=['cdiscount'],
    keywords="Nintendo Switch",
    limit=5,
    test_product_pages=True
)
```

Or execute from terminal within notebook:

```python
!xvfb-run -a python /app/test_cdiscount_product_pages.py "Nintendo Switch" --limit 2
```

---

## 💡 Tips & Tricks

### 1. Access Environment Variables

```python
import os

print(f"Database: {os.getenv('POSTGRES_DB')}")
print(f"Redis: {os.getenv('REDIS_URL')}")
```

### 2. Query the Database

```python
from libs.common.db import SessionLocal
from libs.common.models import ProductTemplate, ListingObservation

with SessionLocal() as db:
    # Get all products
    products = db.query(ProductTemplate).limit(10).all()
    
    for p in products:
        print(f"{p.name}: {p.search_query}")
    
    # Get recent listings
    listings = db.query(ListingObservation).limit(20).all()
    print(f"\nTotal listings in DB: {db.query(ListingObservation).count()}")
```

### 3. Debug Playwright

```python
from libs.common.scraping import ScrapingSession

# Create session with custom settings
session = ScrapingSession(
    use_playwright=True,
    headless=True,  # Set to False to see browser
    enable_stealth=True
)

# Fetch a page
html = await session.get_html_with_playwright("https://www.cdiscount.com")
print(f"Downloaded {len(html)} bytes")
```

### 4. Async Functions

Jupyter notebooks support top-level `await`:

```python
# This works directly - no need for asyncio.run()
results = await connector.search_items("query", limit=10)
```

---

## 🐛 Troubleshooting

### Jupyter Not Starting

```bash
# Check logs
docker compose logs ingestion --tail 50

# Restart container
docker compose restart ingestion

# Try again
docker compose exec -d ingestion jupyter lab ...
```

### Module Not Found

```python
# Add paths at the top of your notebook
import sys
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/libs')
```

### Port Already in Use

If port 8888 is taken, modify `docker-compose.yml`:

```yaml
ports:
  - "8889:8888"  # Use 8889 instead
```

### Playwright Errors

Make sure you're running in the ingestion container where Playwright is installed.

---

## 📚 Useful Keyboard Shortcuts

- `Shift + Enter`: Run cell and move to next
- `Ctrl + Enter`: Run cell
- `Alt + Enter`: Run cell and insert new below
- `A`: Insert cell above
- `B`: Insert cell below
- `DD`: Delete cell
- `M`: Convert to Markdown
- `Y`: Convert to Code
- `Ctrl + S`: Save notebook

---

## 🎓 Next Steps

1. **Experiment with different connectors** - Try Fnac, BackMarket, Rakuten
2. **Test LLM integration** - Once implemented, experiment with listing refinement
3. **Build custom analyses** - Use pandas for data analysis
4. **Create visualizations** - Use matplotlib/plotly for charts
5. **Develop new features** - Prototype new scraping logic

---

## 🔗 Resources

- **Notebooks Directory**: `/mnt/a/Developpement/market_discovery/notebooks/`
- **Example Notebook**: `scraping_experiments.ipynb`
- **Detailed README**: `notebooks/README.md`
- **Access URL**: http://localhost:8888

---

**Happy Experimenting!** 🚀

Need help? Check the logs:
```bash
docker compose logs ingestion --tail 100 -f
```


