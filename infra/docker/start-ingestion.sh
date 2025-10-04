#!/bin/bash
set -e

echo "Starting Market Discovery Ingestion Service..."

# Activate virtual environment and test basic imports
echo "Testing basic imports..."
python -c "
import sys
sys.path.insert(0, '/home/pwuser/venv/lib/python3.12/site-packages')
sys.path.insert(0, '/app')

try:
    from libs.common.scraping import ScrapingSession, scraping_config
    print('Scraping utilities imported successfully')
except Exception as e:
    print(f'Error importing scraping utilities: {e}')
    exit(1)
"

# Check if Playwright is available and browsers are installed
echo "Checking Playwright setup..."
python -c "
import sys
sys.path.insert(0, '/home/pwuser/venv/lib/python3.12/site-packages')
sys.path.insert(0, '/app')

try:
    import playwright
    from playwright.sync_api import sync_playwright
    print('Playwright is available')

    # Try to launch a browser to verify installation
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
            browser.close()
            print('Playwright browsers are working correctly')
        except Exception as e:
            print(f'Browser test failed: {e}')
            print('Will fall back to HTTP-only scraping')
except ImportError as e:
    print(f'Playwright not available: {e}')
    print('Will use HTTP-only scraping')
" || echo "Playwright check failed, continuing with HTTP-only scraping"

echo "Service is ready to handle web scraping requests"

# Run stealth test if requested
if [ "${RUN_STEALTH_TEST:-false}" = "true" ]; then
    echo "Running browser fingerprinting stealth test..."
    xvfb-run -a python /app/test-stealth-config.py
    echo "Stealth test completed"
    exit 0
fi

# Start the ARQ worker
echo "Starting ARQ worker..."
exec xvfb-run -a arq ingestion.worker.WorkerSettings
