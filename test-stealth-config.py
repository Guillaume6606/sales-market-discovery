"""Minimal stealth browser config test — run via RUN_STEALTH_TEST=true."""

import sys


def main() -> None:
    try:
        from patchright.sync_api import sync_patchright

        with sync_patchright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto("https://bot.sannysoft.com/")
            page.wait_for_timeout(3000)
            results = page.evaluate(
                "() => document.querySelector('#fp2 .result').textContent"
            )
            print(f"Stealth test result: {results}")
            browser.close()
    except Exception as e:
        print(f"Stealth test failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
