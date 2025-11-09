#!/usr/bin/env python3
"""
Test script to evaluate browser fingerprinting stealth using CreepJS and BrowserScan
This script uses ScrapingSession from scraping.py for all web scraping operations
"""
import re
from typing import Dict, Any, List, Optional
import asyncio
import json
import re
import time
from typing import Dict, Any
from loguru import logger

# Import ScrapingSession and configuration from scraping.py
from libs.common.scraping import ScrapingSession, ScrapingConfig, STEALTH_PATCH


async def test_browser_stealth():
    """Test browser fingerprinting using CreepJS and BrowserScan with ScrapingSession"""
    logger.info("Starting browser fingerprinting tests using ScrapingSession...")

    # Create scraping session with stealth configuration
    config = ScrapingConfig()
    config.use_playwright = True  # Enable Playwright for these tests

    async with ScrapingSession(config) as session:

        try:
            # Test CreepJS
            logger.info("Testing CreepJS with ScrapingSession...")
            html_content = await session.get_html_with_playwright("https://abrahamjuliot.github.io/creepjs/")
            results = extract_creepjs_results_from_html(html_content)
            display_results(results, "CreepJS")

        except Exception as e:
            logger.error(f"Error during CreepJS test: {e}")

        # Test BrowserScan
        logger.info("Testing BrowserScan bot detection...")
        try:
            html_content = await session.get_html_with_playwright("https://www.browserscan.net/bot-detection")
            report = extract_browserscan_results_from_html(html_content)

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

def extract_creepjs_results_from_html(html_content: str) -> Dict[str, Any]:
    """Extract fingerprinting results from CreepJS HTML content"""
    results = {}

    try:
        # Extract stealth score (look for percentage or score indicators)
        score_patterns = [
            r'(\d+)%',  # Percentage scores
            r'Score:\s*(\d+)',  # Score indicators
            r'Grade:\s*([A-F])',  # Grade indicators
        ]

        for pattern in score_patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            if matches:
                results['stealth_score'] = matches[0]
                break

        # Try to extract JSON data if available
        try:
            # Look for script tags with JSON data
            script_pattern = r'<script[^>]*>(.*?)</script>'
            scripts = re.findall(script_pattern, html_content, re.DOTALL | re.IGNORECASE)

            for script_content in scripts:
                if 'fingerprint' in script_content.lower() or 'stealth' in script_content.lower():
                    # Try to extract JSON
                    json_match = re.search(r'\{.*\}', script_content, re.DOTALL)
                    if json_match:
                        try:
                            data = json.loads(json_match.group())
                            results['raw_data'] = data
                            # Try to extract fingerprint ID from JSON
                            if 'fingerprint' in data:
                                results['fingerprint_id'] = data['fingerprint']
                            break
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass

        # Extract specific test results from HTML content
        test_results = {}

        # Look for WebRTC results
        webrtc_match = re.search(r'<div[^>]*id=["\']webrtc["\'][^>]*>(.*?)</div>', html_content, re.DOTALL | re.IGNORECASE)
        if webrtc_match:
            test_results['webrtc'] = webrtc_match.group(1).strip()

        # Look for Canvas fingerprinting
        canvas_match = re.search(r'<div[^>]*id=["\']canvas["\'][^>]*>(.*?)</div>', html_content, re.DOTALL | re.IGNORECASE)
        if canvas_match:
            test_results['canvas'] = canvas_match.group(1).strip()

        # Look for WebGL fingerprinting
        webgl_match = re.search(r'<div[^>]*id=["\']webgl["\'][^>]*>(.*?)</div>', html_content, re.DOTALL | re.IGNORECASE)
        if webgl_match:
            test_results['webgl'] = webgl_match.group(1).strip()

        results['test_results'] = test_results

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


def extract_browserscan_results_from_html(html_content: str) -> Dict[str, Any]:
    """
    Extract BrowserScan bot-detection results from HTML content
    Returns:
    {
      "overall": {"result": "normal"|"robot"|None},
      "sections": {
        "Identification and bot detection": [{"name","badge","status_text","value_text"}...],
        "Chrome DevTools Protocol Detection": [...],
        "Native Navigator": [...]
      },
      "flag_reasons": [{"section","name","badge","status_text","value_text"}...]
    }
    """
    try:
        # 1) Extract topline "Test Results: Normal|Robot"
        top_result = extract_browserscan_top_result(html_content)

        # 2) Extract sections from HTML
        sections = extract_browserscan_sections(html_content)

        # 3) Calculate flag reasons
        flag_reasons = calculate_browserscan_flags(sections)

        return {
            "overall": {"result": top_result},
            "sections": sections,
            "flag_reasons": flag_reasons
        }

    except Exception as e:
        logger.error(f"Error extracting BrowserScan results: {e}")
        return {
            "overall": {"result": None},
            "sections": {},
            "flag_reasons": []
        }

def extract_browserscan_top_result(html_content: str) -> Optional[str]:
    """Extract the top-level Test Results from BrowserScan HTML"""
    try:
        # Look for Test Results pattern
        pattern = r'Test\s*Results\s*:</strong>.*?<strong[^>]*>\s*(Normal|Robot)\s*</strong>'
        match = re.search(pattern, html_content, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).lower()
    except Exception:
        pass

    return None


def extract_browserscan_sections(html_content: str) -> Dict[str, List[Dict[str, Optional[str]]]]:
    """Extract test sections from BrowserScan HTML"""
    sections = {
        'Identification and bot detection': [],
        'Chrome DevTools Protocol Detection': [],
        'Native Navigator': []
    }

    try:
        # This is a simplified extraction - BrowserScan has complex DOM structure
        # For now, we'll extract basic information from the HTML

        # Look for section headers and their content
        section_patterns = {
            'Identification and bot detection': r'<h2[^>]*id=["\']anchor_webdriver["\'][^>]*>.*?</h2>(.*?)(?=<h2|$)',
            'Chrome DevTools Protocol Detection': r'<h2[^>]*id=["\']anchor_cdp["\'][^>]*>.*?</h2>(.*?)(?=<h2|$)',
            'Native Navigator': r'<h2[^>]*id=["\']anchor_navigator["\'][^>]*>.*?</h2>(.*?)(?=<h2|$)'
        }

        for section_name, pattern in section_patterns.items():
            match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
            if match:
                section_content = match.group(1)

                # Extract test cards (simplified)
                card_pattern = r'<h3[^>]*>(.*?)</h3>'
                card_matches = re.findall(card_pattern, section_content, re.IGNORECASE)

                for card_name in card_matches:
                    sections[section_name].append({
                        'name': card_name.strip(),
                        'badge': None,
                        'status_text': None,
                        'value_text': None
                    })

    except Exception as e:
        logger.error(f"Error extracting BrowserScan sections: {e}")

    return sections


def calculate_browserscan_flags(sections: Dict[str, List[Dict[str, Optional[str]]]]) -> List[Dict[str, Any]]:
    """Calculate which tests are flagged in BrowserScan results"""
    flag_reasons = []

    def is_flagged(item: Dict[str, Optional[str]]) -> bool:
        badge = (item.get("badge") or "").lower()
        status = (item.get("status_text") or "").lower()
        # anything not explicitly "status_success" is considered suspicious
        if badge and "status_success" not in badge:
            return True
        if status and status != "normal":
            return True
        return False

    for section_name, items in sections.items():
        for it in items:
            if is_flagged(it):
                flag_reasons.append({ "section": section_name, **it })

    return flag_reasons



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
    print("This test uses ScrapingSession from scraping.py for all web scraping operations")
    print("💡 Tip: For GUI mode, run with: xvfb-run -a python test-stealth-config.py")

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
