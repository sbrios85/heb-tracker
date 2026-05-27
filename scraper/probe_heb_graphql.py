"""
H-E-B GraphQL Probe
-------------------
Run this ONCE on your local machine (not in CI) to confirm we can reach
heb.com/graphql and to discover the operation names + query shapes their
frontend uses.

Usage:
    pip install httpx
    python scraper/probe_heb_graphql.py

Outputs to ./probe_output/ — paste the contents back to Claude so we can
finalize the discovery scraper against the real API shape.

Strategy:
1. GET homepage to seed cookies.
2. Try introspection (likely disabled, but worth checking).
3. Try several plausible store-lookup operation shapes.
4. Try several plausible product-detail operation shapes for a known SKU
   (Central Market Organics Instant Coffee, product ID 1510154).

We're polite: serial requests, real user-agent, 1s between calls.
"""

import json
import sys
import time
from pathlib import Path

import httpx

ENDPOINT = "https://www.heb.com/graphql"
HOMEPAGE = "https://www.heb.com/"
WALDRON_ADDR = "1145 Waldron Rd, Corpus Christi, TX 78418"
ZIP = "78418"

# Pretend to be a real Chrome on Mac
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.heb.com",
    "Referer": "https://www.heb.com/",
}

OUTDIR = Path(__file__).parent.parent / "probe_output"
OUTDIR.mkdir(exist_ok=True)


def save(name: str, data) -> Path:
    p = OUTDIR / name
    if isinstance(data, (dict, list)):
        p.write_text(json.dumps(data, indent=2))
    else:
        p.write_text(str(data))
    return p


def section(title: str):
    print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        # --- Step 1: seed cookies from homepage ---
        section("Step 1: GET homepage to seed cookies")
        r = c.get(HOMEPAGE)
        print(f"  status: {r.status_code}")
        print(f"  cookies received: {list(c.cookies.keys())}")
        save("homepage_headers.json", dict(r.headers))

        # --- Step 2: try a minimal introspection query ---
        section("Step 2: introspection probe (likely disabled)")
        introspection = {
            "query": "{ __schema { queryType { name } } }"
        }
        r = c.post(ENDPOINT, json=introspection)
        print(f"  status: {r.status_code}")
        print(f"  body (first 400 chars): {r.text[:400]}")
        save("introspection_response.json", r.text)

        # --- Step 3: probe likely store-lookup operations ---
        # The frontend has a store finder. Operation name conventions vary;
        # we'll try several common shapes. The endpoint will tell us which
        # exists by either returning data or a "no such query" error.
        section("Step 3: probe store-lookup operations")

        store_candidates = [
            # name, query
            (
                "stores_by_address",
                """query StoresByAddress($address: String!) {
                  stores(address: $address) {
                    id
                    name
                    address1
                    city
                    state
                    zip
                  }
                }""",
            ),
            (
                "storeSearch",
                """query StoreSearch($address: String!) {
                  storeSearch(address: $address) {
                    storeId
                    name
                    address1
                    city
                  }
                }""",
            ),
            (
                "searchStores",
                """query SearchStores($zipCode: String!) {
                  searchStores(zipCode: $zipCode) {
                    storeId
                    storeName
                    address
                  }
                }""",
            ),
            (
                "stores_simple",
                """{ stores { id name } }""",
            ),
        ]

        for name, q in store_candidates:
            print(f"\n  --- trying: {name}")
            payload = {"query": q, "variables": {"address": WALDRON_ADDR, "zipCode": ZIP}}
            try:
                r = c.post(ENDPOINT, json=payload)
                print(f"    status: {r.status_code}")
                body = r.text[:500]
                print(f"    body: {body}")
                save(f"store_probe_{name}.json", r.text)
            except Exception as e:
                print(f"    ERROR: {e}")
            time.sleep(1.0)

        # --- Step 4: try a known product ID to see what product queries look like ---
        # Central Market Organics Instant Coffee = 1510154 (from earlier search results)
        section("Step 4: probe product-detail operations")
        product_candidates = [
            (
                "productDetail",
                """query ProductDetail($productId: String!) {
                  productDetail(productId: $productId) {
                    id
                    name
                    brand
                    price
                  }
                }""",
            ),
            (
                "product",
                """query Product($id: ID!) {
                  product(id: $id) {
                    id
                    name
                    brand
                  }
                }""",
            ),
            (
                "productById",
                """query ProductById($productId: String!) {
                  productById(productId: $productId) {
                    productId
                    displayName
                  }
                }""",
            ),
        ]
        for name, q in product_candidates:
            print(f"\n  --- trying: {name}")
            payload = {
                "query": q,
                "variables": {"productId": "1510154", "id": "1510154"},
            }
            try:
                r = c.post(ENDPOINT, json=payload)
                print(f"    status: {r.status_code}")
                body = r.text[:500]
                print(f"    body: {body}")
                save(f"product_probe_{name}.json", r.text)
            except Exception as e:
                print(f"    ERROR: {e}")
            time.sleep(1.0)

    print("\nAll probe responses saved to:", OUTDIR)


if __name__ == "__main__":
    main()
