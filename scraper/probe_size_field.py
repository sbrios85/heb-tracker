"""
Probe v4: really find "1 oz" for the hydrocortisone.

Prior probes concluded 'no GraphQL field returns size'. This is possibly wrong.
Rather than testing many field names once, this probe tests fewer fields
but shows the FULL response so we can eyeball where "1 oz" actually is.

Focus: hydrocortisone (id=1403504, expected 1 oz)
"""

import json
import re
import sys
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import HEADERS, HOMEPAGE, GRAPHQL_ENDPOINT, WALDRON_STORE_NUMBER

TEST_ID = "1403504"     # hydrocortisone, expected 1 oz
TARGET = str(WALDRON_STORE_NUMBER)


def section(t):
    print(f"\n{'=' * 70}\n  {t}\n{'=' * 70}")


def fresh_client():
    c = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0)
    c.get(HOMEPAGE)
    return c


def post(c, q, v=None):
    payload = {"query": q}
    if v is not None:
        payload["variables"] = v
    return c.post(GRAPHQL_ENDPOINT, json=payload)


def main():
    c = fresh_client()

    # ============================================================
    # 1) Get productDescription in FULL, no truncation
    # ============================================================
    section("1) Full productDescription (untruncated)")
    q = 'query Q($id: String!, $s: String) { getProductById(id: $id, storeId: $s) { fullDisplayName productDescription } }'
    r = post(c, q, {"id": TEST_ID, "s": TARGET})
    body = r.json()
    p = (body.get("data") or {}).get("getProductById") or {}
    name = p.get("fullDisplayName", "")
    desc = p.get("productDescription", "") or ""
    print(f"  fullDisplayName: {name}")
    print(f"\n  productDescription (full, {len(desc)} chars):")
    print("  " + "-" * 66)
    for line in desc.split("\n"):
        print(f"  | {line}")
    print("  " + "-" * 66)
    # Find every "N unit" pattern with wider unit list
    all_matches = re.findall(r'(\d+(?:\.\d+)?)\s*(oz|OZ|fl\.?\s*oz|lb|LB|ct|CT|g|kg|ml|mL|piece|count|pk|pack|inch|in|"|\')', desc, re.I)
    print(f"\n  ALL size-shaped matches in description: {all_matches}")

    # ============================================================
    # 2) Try many more field-name candidates that could hold "1 oz" or "1"
    # ============================================================
    section("2) More field candidates on Product")
    candidates = [
        "shortDescription", "subheading", "subtitle", "sizeLabel", "label",
        "sizeChip", "chip", "badge", "shortSize", "sellSize", "sellSizeText",
        "sellingSize", "displayText", "sellDescription", "shelfLabel",
        "netContentDescription", "netContentString", "netContentLabel",
        "packagingDescription", "packageDescription", "productTitle",
        "titleSuffix", "titleAppend", "sellingUnit", "sellingSizeText",
    ]
    for f in candidates:
        q = f'query Q($id: String!, $s: String) {{ getProductById(id: $id, storeId: $s) {{ {f} }} }}'
        r = post(c, q, {"id": TEST_ID, "s": TARGET})
        body = r.json()
        if "errors" in body and body["errors"]:
            em = body["errors"][0]["message"]
            if "Cannot query field" in em:
                continue
            elif "must have a selection of subfields" in em:
                m = re.search(r"of type [\"']([\w!\[\]]+)[\"']", em)
                inner = m.group(1) if m else "?"
                print(f"  ⊞ {f:30s} → complex type={inner}")
            else:
                print(f"  ? {f:30s} {em[:80]}")
        else:
            val = (body.get("data") or {}).get("getProductById", {}).get(f)
            marker = " ★" if val and ("1 oz" in str(val) or "1oz" in str(val)) else ""
            print(f"  ✓ {f:30s} → {json.dumps(val)[:120]}{marker}")

    # ============================================================
    # 3) Same for SKU
    # ============================================================
    section("3) More field candidates on SKU")
    sku_candidates = [
        "shortDescription", "sellDescription", "shelfLabel", "label",
        "displayLabel", "displayText", "chip", "sizeChip",
        "netContentDescription", "packaging", "packagingText",
        "sellSizeQuantity", "sellSizeUnit", "sellSizeText", "sellSizeDisplay",
        "productSize", "productSizeText", "productSizeAmount",
        "sellingSize", "sellingSizeText", "sellingUnit",
        "unitOfMeasureText", "unitOfMeasureDisplay", "unitOfMeasureLabel",
        "displayValue", "displayName", "shortName",
    ]
    for f in sku_candidates:
        q = f'query Q($id: String!, $s: String) {{ getProductById(id: $id, storeId: $s) {{ SKUs {{ {f} }} }} }}'
        r = post(c, q, {"id": TEST_ID, "s": TARGET})
        body = r.json()
        if "errors" in body and body["errors"]:
            em = body["errors"][0]["message"]
            if "Cannot query field" in em:
                continue
            elif "must have a selection of subfields" in em:
                m = re.search(r"of type [\"']([\w!\[\]]+)[\"']", em)
                inner = m.group(1) if m else "?"
                print(f"  ⊞ SKUs.{f:30s} → complex type={inner}")
            else:
                print(f"  ? SKUs.{f:30s} {em[:80]}")
        else:
            skus = (body.get("data") or {}).get("getProductById", {}).get("SKUs") or []
            val = skus[0].get(f) if skus else None
            marker = " ★" if val and ("1 oz" in str(val) or "1oz" in str(val)) else ""
            print(f"  ✓ SKUs.{f:30s} → {json.dumps(val)[:120]}{marker}")

    # ============================================================
    # 4) Fetch the product page via Chrome-like headers (defeat Cloudflare stub)
    # ============================================================
    section("4) Fetch product page as Chrome desktop, look for '1 oz' in raw HTML")
    slug = "h-e-b-1-maximum-strength-hydrocortisone-ointment"
    url = f"https://www.heb.com/product-detail/{slug}/{TEST_ID}"
    # Chrome full headers
    chrome_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": '"Chromium";v="126", "Not:A-Brand";v="24", "Google Chrome";v="126"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }
    c2 = httpx.Client(headers=chrome_headers, follow_redirects=True, timeout=30.0)
    r = c2.get(url)
    print(f"  status={r.status_code}, size={len(r.text):,}")

    # Look for "1 oz" pattern
    if "1 oz" in r.text or "1oz" in r.text:
        print("  ✓ '1 oz' STRING FOUND IN HTML")
        # Show contexts
        for m in re.finditer(r'1\s*oz', r.text[:len(r.text)], re.I):
            ctx = r.text[max(0, m.start()-100):m.end()+100].replace("\n", " ")
            print(f"    …{ctx}…")
    else:
        print("  ✗ '1 oz' NOT found in raw HTML (page is still gated)")

    # Look for any data blob
    for id_pat in [r'id="__NEXT_DATA__"', r'window\.__NEXT_DATA__', r'__NUXT__',
                   r'__APOLLO_STATE__', r'__INITIAL_STATE__']:
        if re.search(id_pat, r.text):
            print(f"  found data blob: {id_pat}")

    # ============================================================
    # 5) The last resort: try a direct HTTP GET to the H-E-B product page
    #    JSON endpoint if one exists.
    # ============================================================
    section("5) Try likely REST/JSON endpoints for product size data")
    endpoints = [
        f"https://www.heb.com/api/product/{TEST_ID}",
        f"https://www.heb.com/api/products/{TEST_ID}",
        f"https://www.heb.com/api/v1/product/{TEST_ID}",
        f"https://www.heb.com/api/product-detail/{TEST_ID}",
    ]
    for ep in endpoints:
        try:
            rr = c2.get(ep)
            if rr.status_code == 200:
                print(f"  ✓ {ep} → 200 ({len(rr.text)} bytes)")
                if "1 oz" in rr.text or '"1"' in rr.text:
                    print(f"    contains size-like data")
                    print(f"    first 500 chars: {rr.text[:500]}")
            else:
                print(f"  ✗ {ep} → {rr.status_code}")
        except Exception as e:
            print(f"  ✗ {ep} → {type(e).__name__}")


if __name__ == "__main__":
    main()
