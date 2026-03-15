"""End-to-end tests for the Market Discovery application using Playwright."""

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.e2e


def test_backend_api_docs():
    """Backend serves FastAPI docs."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8000/docs")
        page.wait_for_load_state("networkidle")

        assert "swagger" in page.content().lower() or "FastAPI" in page.title()
        page.screenshot(path="/tmp/backend_docs.png", full_page=True)
        print("PASS: Backend API docs accessible")
        browser.close()


def test_backend_health_endpoints():
    """Backend API endpoints respond correctly."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Test categories endpoint
        response = page.goto("http://localhost:8000/categories")
        assert response.status == 200
        body = response.json()
        assert "categories" in body
        print(f"PASS: /categories returns {len(body['categories'])} categories")

        # Test products endpoint
        response = page.goto("http://localhost:8000/products")
        assert response.status == 200
        body = response.json()
        assert "products" in body
        print(f"PASS: /products returns {len(body['products'])} products")

        # Test ingestion status endpoint
        response = page.goto("http://localhost:8000/ingestion/status")
        assert response.status == 200
        body = response.json()
        assert "total_products" in body
        print(f"PASS: /ingestion/status returns status data")

        browser.close()


def test_streamlit_ui_loads():
    """Streamlit UI loads and shows the main dashboard."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8501")
        page.wait_for_load_state("networkidle")

        # Wait for Streamlit to finish rendering
        page.wait_for_timeout(3000)
        page.screenshot(path="/tmp/ui_initial.png", full_page=True)

        content = page.content()
        assert "Market Discovery" in content or "market" in content.lower()
        print("PASS: Streamlit UI loads with Market Discovery title")

        browser.close()


def test_streamlit_navigation_tabs():
    """Streamlit sidebar navigation tabs are present."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8501")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)

        content = page.content()

        # Check for navigation tabs
        assert "Discovery" in content
        assert "Listing Explorer" in content
        assert "Product Setup" in content
        assert "Import New Data" in content
        print("PASS: All 4 navigation tabs present")

        page.screenshot(path="/tmp/ui_nav.png", full_page=True)
        browser.close()


def test_streamlit_product_setup_tab():
    """Can navigate to Product Setup tab and see the form."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8501")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)

        # Click Product Setup tab in sidebar
        product_setup = page.get_by_text("Product Setup")
        assert product_setup.count() > 0, "Product Setup tab not found"
        product_setup.first.click()
        page.wait_for_timeout(2000)

        content = page.content()
        assert "Manage Categories" in content or "Category" in content
        print("PASS: Product Setup tab shows category management")

        page.screenshot(path="/tmp/ui_product_setup.png", full_page=True)
        browser.close()


if __name__ == "__main__":
    test_backend_api_docs()
    test_backend_health_endpoints()
    test_streamlit_ui_loads()
    test_streamlit_navigation_tabs()
    test_streamlit_product_setup_tab()
    print("\nAll e2e tests passed!")
