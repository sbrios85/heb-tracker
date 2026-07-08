"""
Probe: find the GraphQL field(s) that contain package size (e.g. "12 oz").

Strategy:
  1. Query getProductById with a huge selection of candidate fields
     (size, netWeight, packageSize, etc.) and see which ones return non-null.
  2. Also introspect the Product and SKU types to see all their fields.
  3. Fetch the product's HTML page as fallback and grep for the size string
     "12 oz" to see where it lives.

Test product: 583162 (CAFE Olé Texas Pecan Ground Coffee, 12 oz on H-E-B page)
"""

import json
import re
import sys
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import HEADERS, HOMEPAGE, GRAPHQL_ENDPOINT, WALDRON_STORE_NUMBER

TEST_ID = "583162"
TARGET = str(WALDRON_STORE_NUMBER)
EXPECTED_SIZE = "12 oz"


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
    # 1) Bisect candidate scalar fields on Product
    # ============================================================
    section("1) Test candidate scalar fields on Product")
    scalar_candidates = [
        "size", "netWeight", "weight", "packageSize", "productSize",
        "displaySize", "sizeUnit", "measure", "measures", "productMeasure",
        "packageWeight", "netContent", "unitOfMeasure", "sellingUnit",
        "sellingUnitSize", "totalWeight", "grossWeight", "productWeight",
        "unitSize", "packSize", "sellSize", "sellUnit",
    ]
    hits_scalar, complex_hits, unknown = [], [], []
    for f in scalar_candidates:
        q = f'query Q($id: String!, $s: String) {{ getProductById(id: $id, storeId: $s) {{ {f} }} }}'
        r = post(c, q, {"id": TEST_ID, "s": TARGET})
        body = r.json()
        if "errors" in body and body["errors"]:
            em = body["errors"][0]["message"]
            if "Cannot query field" in em:
                unknown.append(f)
            elif "must have a selection of subfields" in em:
                m = re.search(r"of type [\"']([\w!\[\]]+)[\"']", em)
                inner = m.group(1) if m else "?"
                complex_hits.append({"field": f, "type": inner})
                print(f"  ⊞ {f:22s} → complex type={inner}")
            else:
                print(f"  ? {f:22s} {em[:110]}")
        else:
            val = (body.get("data") or {}).get("getProductById", {}).get(f)
            hits_scalar.append({"field": f, "value": val})
            marker = " ★" if val and EXPECTED_SIZE in str(val) else ""
            print(f"  ✓ {f:22s} → {json.dumps(val)[:80]}{marker}")

    # ============================================================
    # 2) Explore complex-type fields (if any)
    # ============================================================
    if complex_hits:
        section("2) Explore complex fields — try common subfields")
        subfield_guesses = [
            "value", "amount", "unit", "unitOfMeasure", "size", "quantity",
            "displayValue", "text", "label", "formatted", "formattedValue",
        ]
        for c_hit in complex_hits:
            fname = c_hit["field"]
            print(f"\n  --- {fname} (type {c_hit['type']}) ---")
            for sub in subfield_guesses:
                q = f'query Q($id: String!, $s: String) {{ getProductById(id: $id, storeId: $s) {{ {fname} {{ {sub} }} }} }}'
                r = post(c, q, {"id": TEST_ID, "s": TARGET})
                body = r.json()
                if "errors" in body and body["errors"]:
                    em = body["errors"][0]["message"]
                    if "Cannot query field" in em:
                        continue
                    else:
                        print(f"    ? {sub}: {em[:70]}")
                else:
                    v = (body.get("data") or {}).get("getProductById", {}).get(fname)
                    marker = " ★" if v and EXPECTED_SIZE in str(v) else ""
                    print(f"    ✓ {sub}: {json.dumps(v)[:90]}{marker}")

    # ============================================================
    # 3) Test candidate fields on SKU (inside SKUs[])
    # ============================================================
    section("3) Test candidate scalar fields on SKU")
    sku_candidates = [
        "size", "netWeight", "weight", "packageSize", "unitSize", "unit",
        "sellSize", "sellingUnit", "sellingUnitSize", "measure", "netContent",
        "packageWeight", "unitOfMeasure", "displaySize",
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
                print(f"  ⊞ SKUs.{f:20s} → complex type={inner}")
            else:
                print(f"  ? SKUs.{f:20s} {em[:80]}")
        else:
            skus = (body.get("data") or {}).get("getProductById", {}).get("SKUs") or []
            val = skus[0].get(f) if skus else None
            marker = " ★" if val and EXPECTED_SIZE in str(val) else ""
            print(f"  ✓ SKUs.{f:20s} → {json.dumps(val)[:90]}{marker}")

    # ============================================================
    # 4) HTML page — where does "12 oz" actually live?
    # ============================================================
    section("4) HTML page: locate the size string 12 oz in raw source")
    slug = "cafe-ol-by-h-e-b-texas-pecan-medium-roast-ground-coffee"
    url = f"https://www.heb.com/product-detail/{slug}/{TEST_ID}"
    r = c.get(url)
    if r.status_code == 200:
        # Grab the __NEXT_DATA__ blob
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
        if m:
            raw = m.group(1)
            # Find all occurrences of "12 oz" or similar patterns
            hits = []
            for pat in [r'"12 oz"', r'"12oz"', r'"12\s*oz"', r'"12 OZ"', r'"12"', r'"OZ"', r'"oz"']:
                for match in re.finditer(pat, raw):
                    ctx_start = max(0, match.start() - 60)
                    ctx_end = min(len(raw), match.end() + 30)
                    ctx = raw[ctx_start:ctx_end].replace("\n", " ")
                    hits.append((pat, ctx))
            print(f"  Found {len(hits)} matches in __NEXT_DATA__:")
            for pat, ctx in hits[:15]:
                print(f"    [{pat}] …{ctx}…")
        else:
            print("  __NEXT_DATA__ not found in page")
    else:
        print(f"  page fetch failed: {r.status_code}")

    print("\n★ = contains 12 oz. That's the field to use.")


if __name__ == "__main__":
    main()
