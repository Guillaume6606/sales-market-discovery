#!/usr/bin/env python3
"""
Test script to evaluate browser fingerprinting stealth using CreepJS and BrowserScan
This script should be run inside the ingestion Docker container
"""
import re
from typing import Dict, Any, List, Optional
import asyncio
import json
import re
import time
from typing import Dict, Any
from patchright.async_api import async_playwright, Browser, Page, BrowserContext
from loguru import logger



SAFE_PATCH = r"""
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


async def test_browser_stealth():
    """Test browser fingerprinting using CreepJS and BrowserScan"""
    logger.info("Starting browser fingerprinting tests...")

    async with async_playwright() as p:

        context = await p.chromium.launch_persistent_context(
            user_data_dir="/tmp/pwuser",
            locale="en-US",
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
        await context.add_init_script(SAFE_PATCH)
        page: Page = await context.new_page()

        try:
            # Navigate to CreepJS
            logger.info("Navigating to CreepJS...")
            await page.goto("https://abrahamjuliot.github.io/creepjs/", wait_until="domcontentloaded")

            # Wait for fingerprinting to complete
            logger.info("Waiting for fingerprinting analysis...")
            await page.wait_for_timeout(10000)  # Wait 10 seconds for analysis
            results = await extract_creepjs_results(page)
            display_results(results, "CreepJS")

        except Exception as e:
            logger.error(f"Error during CreepJS test: {e}")

        # Test BrowserScan
        logger.info("Testing BrowserScan bot detection...")
        try:
            page: Page = await context.new_page()
            await page.goto("https://www.browserscan.net/bot-detection", wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)  # Wait for analysis

            # Extract BrowserScan results
            report  = await fetch_browserscan_full_report(page)
            
            print("Overall:", report["overall"]["result"])
            print("\n— Identification and bot detection —")
            for item in report["sections"]["Identification and bot detection"]:
                print(item)

            print("\n— Chrome DevTools Protocol Detection —")
            for item in report["sections"]["Chrome DevTools Protocol Detection"]:
                print(item)

            print("\n— Native Navigator —")
            for item in report["sections"]["Native Navigator"]:
                print(item)

            print("\nFlag reasons:")
            for r in report["flag_reasons"]:
                print(r)

        except Exception as e:
            logger.error(f"Error during BrowserScan test: {e}")
        finally:
            await context.close()

async def extract_creepjs_results(page) -> Dict[str, Any]:
    """Extract fingerprinting results from CreepJS page"""
    results = {}

    try:
        await page.wait_for_selector('fingerprint', timeout=15000)
        fp_element = await page.query_selector('fingerprint')
        if fp_element:
            fp_text = await fp_element.inner_text()
            results['fingerprint_id'] = fp_text.strip()

        # Extract stealth score (look for percentage or score indicators)
        score_patterns = [
            r'(\d+)%',  # Percentage scores
            r'Score:\s*(\d+)',  # Score indicators
            r'Grade:\s*([A-F])',  # Grade indicators
        ]

        content = await page.content()
        for pattern in score_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                results['stealth_score'] = matches[0]
                break

        # Extract specific test results
        test_results = {}

        # Look for WebRTC results
        webrtc_elem = await page.query_selector('#webrtc')
        if webrtc_elem:
            test_results['webrtc'] = await webrtc_elem.inner_text()

        # Look for Canvas fingerprinting
        canvas_elem = await page.query_selector('#canvas')
        if canvas_elem:
            test_results['canvas'] = await canvas_elem.inner_text()

        # Look for WebGL fingerprinting
        webgl_elem = await page.query_selector('#webgl')
        if webgl_elem:
            test_results['webgl'] = await webgl_elem.inner_text()

        results['test_results'] = test_results

        # Try to extract JSON data if available
        try:
            # Look for script tags with JSON data
            scripts = await page.query_selector_all('script')
            for script in scripts:
                content = await script.inner_text()
                if 'fingerprint' in content.lower() or 'stealth' in content.lower():
                    # Try to extract JSON
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        try:
                            data = json.loads(json_match.group())
                            results['raw_data'] = data
                            break
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Error extracting CreepJS results: {e}")

    return results


def display_results(results: Dict[str, Any], test_name: str = "Test"):
    """Display the fingerprinting test results"""
    print("\n" + "="*60)
    print(f"🎭 {test_name.upper()} FINGERPRINTING TEST RESULTS")
    print("="*60)

    if 'fingerprint_id' in results:
        print(f"🔍 Fingerprint ID: {results['fingerprint_id']}")

    if 'stealth_score' in results:
        print(f"🎯 Stealth Score: {results['stealth_score']}")

    if 'test_results' in results:
        print("\n📊 Individual Test Results:")
        for test, result in results['test_results'].items():
            print(f"  {test.upper()}: {result}")

    if 'raw_data' in results:
        print("\n📄 Raw Data Available: Yes")
        print("   (Check results dictionary for detailed analysis)")

    # Provide interpretation
    print("\n💡 Interpretation:")
    print("  • Lower fingerprint uniqueness = better stealth")
    print("  • Higher scores indicate more detectable fingerprinting")
    print("  • 0% detection = perfect stealth (very rare)")
    print("  • 50%+ detection = moderate fingerprinting")
    print("  • 80%+ detection = highly detectable")

    print("\n" + "="*60)

async def fetch_browserscan_full_report(page: Page) -> Dict[str, Any]:
    """
    Visit BrowserScan bot-detection page and return:
    {
      "overall": {"result": "normal"|"robot"|None},
      "sections": {
        "Identification and bot detection": [{"name","badge","status_text","value_text"}...],
        "Chrome DevTools Protocol Detection": [...],
        "Native Navigator": [...]
      },
      "flag_reasons": [{"section","name","badge","status_text","value_text"}...]
    }
    A card is "flagged" if its badge is not "status_success" OR its status_text is present and != "normal".
    """
    try:

        # 1) Topline "Test Results: Normal|Robot"
        top_result = await _read_top_test_result(page, timeout=10000)

        # 2) Section parsing via a single DOM pass in the page
        data = await page.evaluate("""
        () => {
          const ANCHORS = new Map([
            ['anchor_webdriver', 'Identification and bot detection'],
            ['anchor_cdp', 'Chrome DevTools Protocol Detection'],
            ['anchor_navigator', 'Native Navigator'],
          ]);

          // Collect all h2 (sections) + h3 (cards) in DOM order
          const nodes = Array.from(document.querySelectorAll('h2[id^="anchor_"], h3'));
          let currentKey = null;
          const buckets = { 'Identification and bot detection': [], 'Chrome DevTools Protocol Detection': [], 'Native Navigator': [] };

          // helper: ascend to the nearest ancestor DIV that ALSO has a descendant <svg><use> (the status icon container)
          const findCardRoot = (h3) => {
            let node = h3;
            for (let i = 0; i < 10 && node; i++) {
              if (node.tagName === 'DIV' && node.querySelector('svg use')) return node;
              node = node.parentElement;
            }
            // fallback: walk up a bit more and accept any DIV
            node = h3;
            for (let i = 0; i < 10 && node; i++) {
              if (node.tagName === 'DIV') return node;
              node = node.parentElement;
            }
            return h3;
          };

          const parseCard = (h3) => {
            const name = (h3.textContent || '').trim() || null;

            const root = findCardRoot(h3);
            const useEl = root.querySelector('svg use');
            const href = useEl ? (useEl.getAttribute('xlink:href') || useEl.getAttribute('href') || '') : '';
            const badge = href && href.includes('#') ? href.split('#').pop() : null;

            // the small wrapper around the icon + value
            const wrap = useEl ? (useEl.closest('div') || root) : root;
            const spans = Array.from(wrap.querySelectorAll('span')).map(s => (s.textContent || '').trim()).filter(Boolean);

            // status "Normal"/"Robot" (when present, e.g., in WebDriver/CDP sections)
            const statusSpan = spans.find(t => /^normal$/i.test(t) || /^robot$/i.test(t));
            const status_text = statusSpan ? statusSpan.toLowerCase() : null;

            // For Navigator cards, there is usually no "Normal/Robot" text; use the first span as the value (often inside .tooltip-rel)
            const value_text = status_text ? null : (spans[0] || null);

            return { name, badge, status_text, value_text };
          };

          for (const n of nodes) {
            if (n.tagName === 'H2') {
              const key = n.id || '';
              if (ANCHORS.has(key)) currentKey = ANCHORS.get(key);
              else currentKey = null;
              continue;
            }
            if (n.tagName === 'H3' && currentKey) {
              buckets[currentKey].push(parseCard(n));
            }
          }

          return { sections: buckets };
        }
        """)

        sections: Dict[str, List[Dict[str, Optional[str]]]] = data["sections"]

        def is_flagged(item: Dict[str, Optional[str]]) -> bool:
            badge = (item.get("badge") or "").lower()
            status = (item.get("status_text") or "").lower()
            # anything not explicitly "status_success" is considered suspicious
            if badge and "status_success" not in badge:
                return True
            if status and status != "normal":
                return True
            return False

        flag_reasons: List[Dict[str, Any]] = []
        for section_name, items in sections.items():
            for it in items:
                if is_flagged(it):
                    flag_reasons.append({ "section": section_name, **it })

        return {
            "overall": {"result": top_result},
            "sections": sections,
            "flag_reasons": flag_reasons
        }
    finally:
        await page.close()

async def _read_top_test_result(page: Page, timeout: int = 20000) -> Optional[str]:
    # Prefer DOM-based extraction near the "Test Results:" label
    try:
        handle = await page.wait_for_function(
            """
            () => {
              const containers = document.querySelectorAll('div, header, main, section');
              for (const c of containers) {
                const t = (c.textContent || '').toLowerCase();
                if (!t.includes('test results:')) continue;
                // Find the first strong/b after the label with a value
                const strongs = c.querySelectorAll('strong, b');
                let sawLabel = false;
                for (const s of strongs) {
                  const txt = (s.textContent || '').trim();
                  if (!sawLabel && /test\\s*results\\s*:/i.test(txt)) { sawLabel = true; continue; }
                  if (txt) {
                    const v = txt.trim().toLowerCase();
                    if (v === 'normal' || v === 'robot') return v;
                  }
                }
              }
              return null;
            }
            """,
            timeout=timeout
        )
        v = await handle.json_value()
        if v: return v
    except Exception:
        pass

    # Fallbacks
    html = await page.content()
    m = re.search(
        r'Test\\s*Results\\s*:</strong>.*?<strong[^>]*>\\s*(Normal|Robot)\\s*</strong>',
        html, re.IGNORECASE | re.DOTALL
    )
    if m:
        return m.group(1).lower()

    text = await page.evaluate("() => document.body.innerText || ''")
    m = re.search(r'Test\s*Results\s*:\s*(Normal|Robot)', text, re.IGNORECASE)
    return m.group(1).lower() if m else None



def calculate_stealth_rating(results: Dict[str, Any]) -> str:
    """Calculate a stealth rating based on results"""
    if 'stealth_score' not in results:
        return "Unknown"

    try:
        score = float(results['stealth_score'])
        if score < 10:
            return "🟢 Excellent (Very Discreet)"
        elif score < 30:
            return "🟡 Good (Moderately Discreet)"
        elif score < 60:
            return "🟠 Moderate (Some Detection Risk)"
        else:
            return "🔴 Poor (Highly Detectable)"
    except:
        return "Unknown"

if __name__ == "__main__":
    print("🔍 Testing Browser Fingerprinting Stealth...")
    print("This test will visit CreepJS and BrowserScan to analyze how detectable your browser setup is.")
    print("💡 Tip: For GUI mode, run with: xvfb-run -a python test-stealth.py")

    try:
        asyncio.run(test_browser_stealth())
    except KeyboardInterrupt:
        print("\n⚠️ Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        print("💡 Troubleshooting:")
        print("  • Ensure internet connection is available")
        print("  • Check if Playwright browsers are installed")
        print("  • Verify both CreepJS and BrowserScan sites are accessible")
        print("  • Try running with --headless=false for debugging")
