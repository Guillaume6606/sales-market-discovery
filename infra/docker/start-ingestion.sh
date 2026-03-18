#!/bin/bash
set -e

echo "Starting Market Discovery Ingestion Service..."

# Test basic imports
echo "Testing basic imports..."
uv run python -c "
from libs.common.scraping import ScrapingSession, scraping_config
print('Scraping utilities imported successfully')
"

# Check if Playwright is available and browsers are installed
echo "Checking Playwright setup..."
uv run python -c "
from patchright.sync_api import sync_playwright
print('Patchright is available')
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    browser.close()
    print('Patchright browsers are working correctly')
" || echo "Patchright check failed, continuing with HTTP-only scraping"

echo "Service is ready to handle web scraping requests"

# Run stealth test if requested
if [ "${RUN_STEALTH_TEST:-false}" = "true" ]; then
    echo "Running browser fingerprinting stealth test..."
    xvfb-run -a uv run python /app/test-stealth-config.py
    echo "Stealth test completed"
    exit 0
fi

# Start the ARQ worker
echo "Starting ARQ worker..."
exec xvfb-run -a uv run arq ingestion.worker.WorkerSettings
