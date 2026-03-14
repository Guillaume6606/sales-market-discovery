"""
Screenshot capture service using Playwright.
"""

import uuid
from datetime import datetime
from pathlib import Path

from loguru import logger

from libs.common.settings import settings

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not available. Screenshot capture will be disabled.")


def _ensure_screenshot_directory() -> Path:
    """Ensure screenshot storage directory exists."""
    storage_path = Path(settings.screenshot_storage_path)
    storage_path.mkdir(parents=True, exist_ok=True)
    return storage_path


async def capture_listing_screenshot(url: str, listing_id: str, source: str) -> str | None:
    """
    Capture screenshot of a listing page.

    Args:
        url: URL of the listing page
        listing_id: Unique listing identifier
        source: Source marketplace (ebay, leboncoin, vinted, etc.)

    Returns:
        Path to saved screenshot file, or None if capture failed
    """
    if not settings.screenshot_enabled:
        logger.debug("Screenshot capture disabled")
        return None

    if not PLAYWRIGHT_AVAILABLE:
        logger.warning("Playwright not available, cannot capture screenshot")
        return None

    if not url:
        logger.warning("No URL provided for screenshot capture")
        return None

    try:
        storage_dir = _ensure_screenshot_directory()

        # Generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{source}_{listing_id}_{timestamp}_{uuid.uuid4().hex[:8]}.png"
        file_path = storage_dir / filename

        # Capture screenshot with Playwright
        async with async_playwright() as p:
            # Launch browser (headless)
            browser = await p.chromium.launch(headless=True)

            try:
                # Create browser context with user-agent and viewport
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                )
                page = await context.new_page()

                # Navigate to URL with timeout
                try:
                    await page.goto(url, wait_until="networkidle", timeout=30000)
                except PlaywrightTimeoutError:
                    logger.warning(f"Timeout loading {url}, taking screenshot anyway")
                    # Still try to capture what's loaded

                # Wait a bit for dynamic content
                await page.wait_for_timeout(2000)

                # Marketplace-specific handling
                if source == "ebay":
                    # Wait for main content
                    try:
                        await page.wait_for_selector("#vi-lkhdr", timeout=5000)
                    except PlaywrightTimeoutError:
                        pass
                elif source == "leboncoin":
                    # Wait for main content
                    try:
                        await page.wait_for_selector("[data-qa-id='adview_title']", timeout=5000)
                    except PlaywrightTimeoutError:
                        pass
                elif source == "vinted":
                    # Wait for main content
                    try:
                        await page.wait_for_selector(".item-details", timeout=5000)
                    except PlaywrightTimeoutError:
                        pass

                # Take screenshot
                await page.screenshot(path=str(file_path), full_page=True)

                # Get file size
                file_size = file_path.stat().st_size

                logger.info(
                    f"Captured screenshot: {file_path} ({file_size} bytes) for {source} listing {listing_id}"
                )

                return str(file_path)

            except Exception as e:
                logger.error(f"Error capturing screenshot for {url}: {e}", exc_info=True)
                return None
            finally:
                await context.close()
                await browser.close()

    except Exception as e:
        logger.error(f"Failed to capture screenshot: {e}", exc_info=True)
        return None


def delete_screenshot(file_path: str) -> bool:
    """
    Delete a screenshot file.

    Args:
        file_path: Path to screenshot file

    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        path = Path(file_path)
        if path.exists():
            path.unlink()
            logger.debug(f"Deleted screenshot: {file_path}")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to delete screenshot {file_path}: {e}")
        return False
