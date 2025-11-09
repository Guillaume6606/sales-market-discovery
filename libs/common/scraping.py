"""
Advanced web scraping utilities with anti-bot detection bypass
"""
import asyncio
import json
import os
import random
import time
import asyncio, random
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import httpx
from loguru import logger
from fake_useragent import UserAgent
import cloudscraper
from patchright.async_api import async_playwright, Browser, Page, BrowserContext, PlaywrightContextManager, TimeoutError as PWTimeout
import re
from .settings import settings

# Advanced browser fingerprinting patch from test-stealth.py
STEALTH_PATCH = r"""
    (() => {
    const NativePC = window.RTCPeerConnection;
    window.RTCPeerConnection = function(cfg = {}, ...rest) {
        cfg = {...cfg, iceTransportPolicy: 'relay'};
        return new NativePC(cfg, ...rest);
    };
    const tryDefine = (obj, prop, getter) => {
        try {
        const d = Object.getOwnPropertyDescriptor(obj, prop);
        if (!d || d.configurable) {
            Object.defineProperty(obj, prop, { get: getter, configurable: true });
            return true;
        }
        } catch(_) {}
        return false;
    };

    const patchNav = () => {
        const proto = Object.getPrototypeOf(navigator); // Navigator.prototype (usually)
        const tryNav = (prop, getter) =>
        tryDefine(proto, prop, getter) || tryDefine(navigator, prop, getter);

        // Storage/memory/network (plausible)
        try {
        const est = navigator.storage.estimate.bind(navigator.storage);
        navigator.storage.estimate = () => est().then(e => ({...e, quota: 16e9}));
        } catch(_) {}
        tryDefine(performance, 'memory', () => ({
        totalJSHeapSize: 3e8, usedJSHeapSize: 1.5e8, jsHeapSizeLimit: 6e8
        }));
    };

    const patchPlugins = () => {
        try {
        const MimeTypeArray = function(){}, PluginArray = function(){};
        // @ts-ignore
        const mtProto = MimeType?.prototype || Object.prototype;
        // @ts-ignore
        const plProto = Plugin?.prototype || Object.prototype;
        const pdfMime = Object.assign(Object.create(mtProto), {type:'application/pdf', suffixes:'pdf', description:''});
        const chromePDF = Object.assign(Object.create(plProto), {name:'Chrome PDF Viewer', filename:'internal-pdf-viewer', description:'', length:1});
        const mt = Object.assign(new MimeTypeArray(), {0: pdfMime, length: 1});
        const pl = Object.assign(new PluginArray(),   {0: chromePDF, length: 1});
        Object.defineProperty(navigator, 'mimeTypes', { get: () => mt });
        Object.defineProperty(navigator, 'plugins',   { get: () => pl });
        } catch(_) {}
    };

    const patchWebGL = () => {
        const patchGL = (Ctx) => {
        if (!Ctx) return;
        const GP = Ctx.prototype.getParameter;
        if (typeof GP !== 'function') return;
        Ctx.prototype.getParameter = function(p){
            try {
            if (p === 37445) return 'Google Inc.'; // UNMASKED_VENDOR_WEBGL
            if (p === 37446) return 'ANGLE (Intel, Mesa Intel(R) UHD Graphics, OpenGL 4.6)';
            } catch(_) {}
            return GP.call(this, p);
        };
        };
        patchGL(window.WebGLRenderingContext);
        patchGL(window.WebGL2RenderingContext);
    };

    const patchCanvasAudio = () => {
        try {
        const gID = CanvasRenderingContext2D.prototype.getImageData;
        CanvasRenderingContext2D.prototype.getImageData = function(x,y,w,h){
            const d = gID.call(this,x,y,w,h);
            for (let i=0;i<d.data.length;i+=4999) d.data[i]^=0;
            return d;
        };
        } catch(_) {}
        try {
        const gCD = AudioBuffer.prototype.getChannelData;
        AudioBuffer.prototype.getChannelData = function(c){
            const d = gCD.call(this,c).slice(0);
            for (let i=0;i<d.length;i+=8191) d[i]+=1e-7;
            return d;
        };
        } catch(_) {}
        // OffscreenCanvas (used in workers)
        try {
        const OC2D = OffscreenCanvasRenderingContext2D?.prototype;
        if (OC2D?.getImageData) {
            const og = OC2D.getImageData;
            OC2D.getImageData = function(x,y,w,h){
            const d = og.call(this,x,y,w,h);
            for (let i=0;i<d.data.length;i+=4999) d.data[i]^=0;
            return d;
            };
        }
        } catch(_) {}
    };

    patchNav();
    patchPlugins();
    patchWebGL();
    patchCanvasAudio();
    })();
"""


class ScrapingConfig:
    """Configuration for scraping operations with stealth settings"""

    def __init__(self):
        self.use_proxies = False
        self.proxy_list: List[str] = []
        self.min_delay = 1.0
        self.max_delay = 3.0
        self.max_retries = 3
        self.timeout = 30.0
        self.use_playwright = settings.use_playwright

class ScrapingSession:
    """Advanced scraping session with anti-bot detection bypass"""

    def __init__(self, config: Optional[ScrapingConfig] = None):
        self.config = config or ScrapingConfig()
        self.session = None
        self.ua_generator = UserAgent()
        self._last_request_time = 0
        self._request_count = 0
        self._playwright_context: Optional[BrowserContext] = None
        self._playwright_instance: Optional[PlaywrightContextManager] = None

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()

    async def initialize(self):
        """Initialize scraping session"""
        # Create cloudscraper session for basic anti-bot bypass
        self.session = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )

        # Set default headers
        self.session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })

        # Initialize Playwright with stealth configuration if needed
        if self.config.use_playwright:
            self._playwright_instance = await async_playwright().start()

            # Use persistent context with stealth configuration (from test-stealth.py)
            self._playwright_context = await self._playwright_instance.chromium.launch_persistent_context(
                user_data_dir="/tmp/pwuser",
                locale="fr-FR",
                timezone_id="Europe/Paris",
                geolocation={"latitude": 48.8566, "longitude": 2.3522},
                channel="chrome",      # requires: patchright install chrome
                headless=False,        # run with xvfb-run in CI if needed
                no_viewport=True,       # use the OS window size 
                service_workers="block",
                args=[
                    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--webrtc-stun-probe-trial=disabled",
                    "--use-fake-device-for-media-stream",
                    "--use-fake-ui-for-media-stream",
                ]
                # IMPORTANT: do NOT set user_agent or extra headers here
            )

            # Apply stealth patch
            await self._playwright_context.add_init_script(STEALTH_PATCH)

    async def cleanup(self):
        """Cleanup resources"""
        if self.session:
            self.session.close()

        if self._playwright_context:
            await self._playwright_context.close()

        if self.config.use_playwright:
            await self._playwright_instance.stop()

    async def _apply_random_delay(self):
        """Apply random delay between requests"""
        delay = random.uniform(self.config.min_delay, self.config.max_delay)
        await asyncio.sleep(delay)

    async def get_with_retry(self, url: str, **kwargs) -> httpx.Response:
        """Make HTTP request with retry logic (simple fallback for Playwright failures)"""
        last_exception = None

        for attempt in range(self.config.max_retries):
            try:
                # Apply delay between attempts
                if attempt > 0:
                    delay = min(2 ** attempt, 10)  # Exponential backoff
                    await asyncio.sleep(delay)

                # Apply delay between requests
                await self._apply_random_delay()

                # Use cloudscraper which handles headers automatically
                response = self.session.get(url, timeout=self.config.timeout, **kwargs)

                # Check for bot detection
                if self._is_bot_detected(response):
                    logger.warning(f"Bot detection detected for {url}, attempt {attempt + 1}")
                    if attempt == self.config.max_retries - 1:
                        raise Exception("Bot detection detected after all retries")
                    continue

                self._request_count += 1
                return response

            except Exception as e:
                last_exception = e
                logger.warning(f"Request attempt {attempt + 1} failed for {url}: {e}")

        raise last_exception

    def _is_bot_detected(self, response: httpx.Response) -> bool:
        """Check if response indicates bot detection"""
        # Common bot detection indicators
        bot_indicators = [
            "blocked",
            "forbidden",
            "access denied",
            "captcha",
            "challenge",
            "verify you are human",
            "suspicious activity",
            "rate limit",
            "too many requests"
        ]

        content = response.text.lower()
        status_code = response.status_code

        # Check status codes
        if status_code in [403, 429, 503]:
            return True

        # Check content for bot indicators
        for indicator in bot_indicators:
            if indicator in content:
                return True

        return False

    async def get_html_with_playwright(self, url: str, wait_until: str = "networkidle", timeout: int = 60000) -> str:
        """Get HTML content using Playwright (handles JS + common consent banners).
        
        Args:
            url: URL to fetch
            wait_until: When to consider navigation successful ('load', 'domcontentloaded', 'networkidle')
            timeout: Maximum time to wait in milliseconds
        """
        if not self._playwright_context:
            raise Exception("Playwright not available - falling back to HTTP request")

        page: Page = await self._playwright_context.new_page()

        # --- helpers --------------------------------------------------------------
        async def _click_if(btn, page):
            """Click the first matching locator if present; return True if clicked."""
            try:
                if await btn.count():
                    await btn.scroll_into_view_if_needed()
                    await btn.hover()
                    await page.wait_for_timeout(120 + random.randrange(60))
                    await btn.click(delay=35 + random.randrange(40))
                    return True
            except Exception:
                pass
            return False

        async def _try_consent_clicks() -> bool:
            """
            Try to accept/dismiss common CMPs (first on the main page, then iframes).
            Returns True if any click happened.
            """
            # Common accept buttons/selectors across CMPs (FR + EN variants).
            selectors = [
                # Didomi (used by leboncoin)
                "#didomi-notice-agree-button",
                "pierce/#didomi-notice-agree-button"
                'button:has-text("Accepter")',
                'button:has-text("J’accepte")',      # curly apostrophe
                "button:has-text(\"J'accepte\")",    # straight apostrophe
                'button:has-text("Tout accepter")',
                'button:has-text("Accepter & Fermer")',
                'button:has-text("Accepter & Fermer →")',
                "pierce/button:has-text('Accepter')",
                "pierce/button:has-text(\"J’accepte\")",
                "pierce/button:has-text(\"J'accepte\")",
                "pierce/button:has-text('Accept & Close')",
                "pierce/button:has-text('Accept & Close →')",
                "pierce/button:has-text('Tout accepter')",
                "pierce/button:has-text('Accept all')",

                # OneTrust
                "#onetrust-accept-btn-handler",
                "button#onetrust-accept-btn-handler",
                "button:has-text('Tout accepter')",

                # Sourcepoint / Quantcast
                'button[title="Accept All"]',
                'button:has-text("Accept all")',
                'button:has-text("Agree")',
                'button:has-text("I agree")',
                'button:has-text("J\'accepte tout")',
                'button:has-text("Accepter tout")',

                # TrustArc
                "#truste-consent-button",
                ".truste-button2",

                # Cookiebot
                "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
            ]
            await page.wait_for_function("window.didomi || window.Didomi", timeout=5000)
            clicked = False
            didomi_wall = await page.evaluate("""
                !!(document.body && document.body.classList.contains('didomi-popup-open'))
                || (window.didomi?.notice?.isVisible?.() === true)
                """)

            # 1) Try in the main document
            tries = 0
            for s in selectors:
                if await _click_if(page.locator(s).first, page):
                    clicked = True
                    logger.info(f"Clicked {s}")
                    await asyncio.sleep(3)
                    await asyncio.sleep(random.uniform(0.2, 0.5))
                    tries += 1
                    if tries > 3:
                        break

            # Check if consent wall is still visible
            didomi_wall = await page.evaluate("""
                !!(document.body && document.body.classList.contains('didomi-popup-open'))
                || (window.didomi?.notice?.isVisible?.() === true)
                """)

            if didomi_wall:
                frame_tries = 0
                for frame in page.frames:
                    for s in selectors:
                        if await _click_if(frame.locator(s).first, page):
                            clicked = True
                            logger.info(f"Clicked {s} in iframe")
                            await asyncio.sleep(3)
                            await asyncio.sleep(random.uniform(0.2, 0.5))
                            frame_tries += 1
                            if frame_tries > 3:
                                break
                    if clicked:
                        break
            didomi_wall = await page.evaluate("""
                !!(document.body && document.body.classList.contains('didomi-popup-open'))
                || (window.didomi?.notice?.isVisible?.() === true)
                """)
            if didomi_wall:
                raise RuntimeError("Consent wall still visible after handling.")
            return clicked

        async def _humanize_and_settle():
            """Small realistic interactions + settle network to help hydration."""
            # gentle jitter before/after consent
            await asyncio.sleep(random.uniform(0.4, 1.2))
            # light scroll to trigger lazy content
            try:
                for _ in range(random.randint(2, 5)):
                    await page.mouse.wheel(0, random.randint(800, 1600))
                    await asyncio.sleep(random.uniform(0.2, 0.5))
                # scroll back a bit
                await page.mouse.wheel(0, -random.randint(400, 900))
            except Exception:
                pass
            # let things settle
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeout:
                # fall back to a short fixed delay
                await asyncio.sleep(random.uniform(0.8, 1.6))

        # --- main flow ------------------------------------------------------------
        try:
            # Add random delay before navigation
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # Navigate and wait for network to be idle (complete loading including AJAX)
            # Increased timeout to 60s for heavy sites with anti-bot protection
            response = await page.goto(url, wait_until=wait_until, timeout=timeout)
            if response and response.status in (403, 429):
                raise Exception(f"Bot detection detected: {response.status}")

            # Attempt to accept consent (best-effort; don't fail if not present)
            try:
                # Fast path: if consent UI appears quickly
                await _try_consent_clicks()
            except Exception:
                pass

            # Give the page a moment, then try consent again (some CMPs appear late)
            await asyncio.sleep(random.uniform(0.6, 1.2))
            try:
                await _try_consent_clicks()
            except Exception:
                pass

            # Human-like interactions and settle
            await _humanize_and_settle()

            # Final small delay to ensure DOM is stable
            await asyncio.sleep(random.uniform(0.5, 1.0))

            # Return final HTML
            html_content = await page.content()
            return html_content

        finally:
            await page.close()

    async def get_html_with_fallback(self, url: str) -> str:
        """Get HTML content with fallback from Playwright to HTTP"""
        try:
            return await self.get_html_with_playwright(url)
        except Exception as e:
            logger.warning(f"Playwright failed: {e}, falling back to HTTP request")
            response = await self.get_with_retry(url)
            response.raise_for_status()
            # Requests/cloudscraper should auto-decompress, but we'll handle it explicitly if needed
            try:
                # First check if the response is compressed
                content_encoding = response.headers.get('Content-Encoding', '').lower()
                
                if 'gzip' in content_encoding or 'br' in content_encoding:
                    # Response claims to be compressed but might not have been decompressed
                    # Force decompression using gzip module
                    import gzip
                    try:
                        decompressed = gzip.decompress(response.content)
                        encoding = response.encoding or 'utf-8'
                        html = decompressed.decode(encoding, errors='replace')
                        logger.debug(f"Manually decompressed gzip response with encoding: {encoding}")
                        return html
                    except Exception as gzip_error:
                        logger.debug(f"Gzip decompression failed: {gzip_error}, trying direct decode")
                
                # Try regular text access (requests should have auto-decompressed)
                html = response.text
                logger.debug(f"Got HTML via response.text, length: {len(html)}")
                return html
                
            except Exception as decode_error:
                logger.error(f"All decoding attempts failed: {decode_error}")
                # Last resort - return raw response.text
                return response.text

class ScrapingUtils:
    """Utility functions for scraping operations"""

    @staticmethod
    def extract_price(text: str) -> Optional[float]:
        """Extract price from text"""
        if not text:
            return None

        # Remove common separators and extract numbers
        price_patterns = [
            r'(\d+(?:\s?\d{3})*(?:[.,]\d{2})?)',  # 1,234.56 or 1234.56 or 1 234,56
            r'(\d+(?:[.,]\d{2}))'  # Simple decimal format
        ]

        for pattern in price_patterns:
            matches = re.findall(pattern, text.replace(' ', ''))
            if matches:
                # Clean the match and convert to float
                price_str = matches[0].replace(' ', '').replace(',', '.')
                try:
                    return float(price_str)
                except ValueError:
                    continue

        return None

    @staticmethod
    def extract_location(text: str) -> Optional[str]:
        """Extract location from text"""
        if not text:
            return None

        # Common French location patterns
        location_patterns = [
            r'(\d{5}\s+[A-Za-z\s-]+)',  # Postal code + city
            r'([A-Za-z\s-]+(?:\d{5})?)',  # City name patterns
        ]

        for pattern in location_patterns:
            matches = re.findall(pattern, text)
            if matches:
                return matches[0].strip()

        return None

    @staticmethod
    def extract_date(text: str) -> Optional[datetime]:
        """Extract date from text"""
        if not text:
            return None

        # French date patterns
        date_patterns = [
            r'(\d{1,2}/\d{1,2}/\d{4})',  # DD/MM/YYYY
            r'(\d{1,2}-\d{1,2}-\d{4})',  # DD-MM-YYYY
            r'(\d{4}-\d{1,2}-\d{1,2})',  # YYYY-MM-DD
        ]

        for pattern in date_patterns:
            matches = re.findall(pattern, text)
            if matches:
                date_str = matches[0]
                for fmt in ['%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d']:
                    try:
                        return datetime.strptime(date_str, fmt)
                    except ValueError:
                        continue

        return None

    @staticmethod
    def clean_text(text: str) -> str:
        """Clean and normalize text"""
        if not text:
            return ""

        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text.strip())

        # Remove HTML entities
        text = re.sub(r'&[a-zA-Z]+;', ' ', text)

        return text.strip()

# Global instances
scraping_config = ScrapingConfig()
scraping_utils = ScrapingUtils()
