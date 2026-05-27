"""
H-E-B GraphQL Probe — Phase 2
-----------------------------
Phase 1 confirmed:
  - GraphQL endpoint at https://www.heb.com/graphql works from GitHub Actions IPs
  - `productDetail` is a real Query field returning type `ProductDetailResult`
  - Other guesses (stores, storeSearch, product, productById) DO NOT exist
  - Introspection is disabled

Phase 2 goals:
  A) Discover the correct argument name for `productDetail` (it's not `productId`).
  B) Discover the actual field names on `ProductDetailResult`.
  C) Find the store-lookup operation name (none of our guesses matched).
  D) Extract JS bundle URLs from the homepage so we can read the real Apollo
     queries hard-coded in H-E-B's frontend code.

Strategy:
  - GraphQL errors leak schema info. Asking for the wrong field returns
    "Cannot query field X on type Y" which confirms Y is the right type.
  - Some Apollo servers include "Did you mean ..." suggestions. We try
    many guesses to see if any trigger suggestions.
  - For the store lookup: try the common Apollo conventions used by other
    grocery sites (Kroger, Walmart, etc.) and H-E-B-specific naming.
  - Apollo's persisted-query system means the frontend code contains the
    exact query strings. We download the homepage HTML, find the JS bundle
    URLs, save them for inspection.
"""

import json
import re
import time
from pathlib import Path

import httpx

ENDPOINT = "https://www.heb.com/graphql"
HOMEPAGE = "https://www.heb.com/"

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


def save(name, data):
    p = OUTDIR / name
    if isinstance(data, (dict, list)):
        p.write_text(json.dumps(data, indent=2))
    else:
        p.write_text(str(data))
    return p


def section(title):
    print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")


def post_query(client, query, variables=None, op_name=None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    if op_name:
        payload["operationName"] = op_name
    return client.post(ENDPOINT, json=payload)


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        # ----- Setup: seed cookies + save HTML -----
        section("Setup: seed cookies from homepage and save HTML")
        r = c.get(HOMEPAGE)
        print(f"  homepage status: {r.status_code}, cookies: {list(c.cookies.keys())}")
        homepage_html = r.text
        save("homepage.html", homepage_html)
        print(f"  homepage HTML size: {len(homepage_html)} bytes")

        # ----- A) productDetail argument name -----
        section("A) productDetail: discover correct argument name")
        arg_candidates = [
            "id", "productId", "productID", "sku", "upc", "ean",
            "code", "productCode", "slug", "key", "uid", "pid",
        ]
        for arg in arg_candidates:
            q = f'query Q($v: String!) {{ productDetail({arg}: $v) {{ __typename }} }}'
            r = post_query(c, q, {"v": "1510154"})
            try:
                err = r.json()["errors"][0]["message"]
            except Exception:
                err = r.text[:200]
            print(f"  arg={arg:15s} status={r.status_code} err={err[:180]}")
            save(f"arg_{arg}.json", r.text)
            time.sleep(0.5)

        # ----- B) ProductDetailResult fields -----
        section("B) ProductDetailResult: probe field names (using arg=id)")
        field_candidates = [
            "id", "productId", "sku", "upc", "ean",
            "name", "displayName", "title", "productName",
            "brand", "brandName", "manufacturer",
            "price", "regularPrice", "salePrice", "currentPrice", "listPrice", "unitPrice",
            "image", "imageUrl", "images", "thumbnail",
            "description", "longDescription", "shortDescription",
            "available", "availability", "inStock", "isAvailable", "stockStatus",
            "category", "categories", "taxonomy", "department",
            "size", "weight", "unit", "uom",
            "url", "slug", "path",
            "ingredients", "nutrition", "nutritionFacts",
        ]
        for field in field_candidates:
            q = f'query Q {{ productDetail(id: "1510154") {{ {field} }} }}'
            r = post_query(c, q)
            try:
                err = r.json()["errors"][0]["message"]
            except Exception:
                err = r.text[:200]
            status_marker = "SUCCESS" if r.status_code == 200 and "errors" not in r.text else "ERR"
            print(f"  field={field:25s} {status_marker:8s} err={err[:160]}")
            save(f"field_{field}.json", r.text)
            time.sleep(0.3)

        # ----- C) store lookup operation name -----
        section("C) Store lookup: probe operation names")
        store_op_candidates = [
            "store", "stores", "storeLookup", "storeFinder", "storesByZip",
            "storesNearMe", "findStores", "nearestStores", "locator",
            "storeLocator", "getStore", "getStores", "fetchStores",
            "storesByAddress", "storesByCoords", "storesByLatLng",
        ]
        for op in store_op_candidates:
            q = f'query Q {{ {op} {{ __typename }} }}'
            r = post_query(c, q)
            try:
                err = r.json()["errors"][0]["message"]
            except Exception:
                err = r.text[:200]
            print(f"  op={op:22s} status={r.status_code} err={err[:160]}")
            save(f"store_op_{op}.json", r.text)
            time.sleep(0.3)

        # ----- D) JS bundle URLs from homepage -----
        section("D) Extract JS bundle URLs from homepage")
        js_urls = re.findall(r'<script[^>]+src="([^"]+\.js[^"]*)"', homepage_html)
        next_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', homepage_html, re.DOTALL)
        print(f"  found {len(js_urls)} <script src> tags")
        for u in js_urls[:30]:
            print(f"    {u}")
        save("js_bundle_urls.json", js_urls)
        if next_data:
            print("  __NEXT_DATA__ blob found! (Next.js app)")
            save("next_data.json", next_data.group(1))
        else:
            print("  no __NEXT_DATA__ blob")

    print(f"\nAll probe responses saved to: {OUTDIR}")


if __name__ == "__main__":
    main()
