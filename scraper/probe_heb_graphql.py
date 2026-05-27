"""
H-E-B GraphQL Probe — Phase 15 (FINAL probe)
--------------------------------------------
Phase 14 found __NEXT_DATA__.props.pageProps.product contains the full
product on every PDP, with all fields we want.

This probe just confirms by dumping the full product JSON for 3 products.

After this we stop probing and write the actual scraper.
"""

import json
import re
import time
from pathlib import Path
import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
OUTDIR = Path(__file__).parent.parent / "probe_output"
OUTDIR.mkdir(exist_ok=True)


def safe(s):
    return re.sub(r'[^A-Za-z0-9_\-.]', '_', str(s))[:80]


def save(name, data):
    p = OUTDIR / safe(name)
    p.write_text(json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data))


def section(t):
    print(f"\n{'=' * 70}\n  {t}\n{'=' * 70}")


def fetch_product(client, slug, product_id):
    url = f"https://www.heb.com/product-detail/{slug}/{product_id}"
    print(f"\n  GET {url}")
    r = client.get(url, timeout=30.0)
    print(f"    status: {r.status_code}, size: {len(r.text)}")
    if r.status_code != 200:
        return None
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
    if not m:
        print("    no __NEXT_DATA__!")
        return None
    try:
        nd = json.loads(m.group(1))
        product = nd.get("props", {}).get("pageProps", {}).get("product")
        return product
    except json.JSONDecodeError as e:
        print(f"    parse error: {e}")
        return None


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        # Seed cookies
        c.get("https://www.heb.com/")

        # We have 3 confirmed CAFE Olé products from earlier probes
        products = [
            ("cafe-ol-by-h-e-b-texas-pecan-medium-roast-ground-coffee", "583162"),
            ("cafe-ol-by-h-e-b-texas-pecan-medium-roast-coffee-single-serve-cups", "1604429"),
            ("cafe-ol-by-h-e-b-donut-shop-texas-pecan-taste-of-san-antonio-coffee-single-serve-cups-variety-pack", "1707137"),
        ]

        section("Pull full product JSON from __NEXT_DATA__ for 3 products")
        for slug, pid in products:
            product = fetch_product(c, slug, pid)
            if not product:
                continue
            save(f"FINAL_product_{pid}.json", product)
            print(f"\n    === FULL PRODUCT {pid} ===")
            print(json.dumps(product, indent=2)[:6000])
            time.sleep(1.0)

    print(f"\nDone. Output in {OUTDIR}")


if __name__ == "__main__":
    main()
