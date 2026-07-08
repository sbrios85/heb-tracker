"""
Probe v2: find the size *quantity* field.

Probe v1 found SKUs.unitOfMeasure = "OUNCE" but not the number 12.
The 12 must live in a paired field. Also __NEXT_DATA__ was missing from
the fetched page — need to try alternate fetch patterns.
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
    # 1) Broader SKU scalar-field probe (a lot more names to try)
    # ============================================================
    section("1) SKU scalar-field probe (broader)")
    sku_candidates = [
        "size", "sellSize", "sellingSize", "sellSizeAmount",
        "netContent", "netContentAmount", "netContentValue", "netContentDisplay",
        "quantity", "quantityAmount", "quantityValue",
        "packageSize", "packSize", "productSize", "productPackageSize",
        "unitSize", "unitAmount", "unitValue", "unitCount",
        "measure", "measureValue", "measureAmount",
        "netWeight", "grossWeight", "weight", "weightValue", "weightAmount",
        "displaySize", "displayValue", "displayAmount",
        "servingSize", "count", "containerSize",
        "sellingUnitOfMeasure", "unitOfMeasureQuantity", "unitOfMeasureAmount",
        "productDescriptionSize", "productWeight",
        "netWeightUOM", "sellSizeUOM", "sellDescription",
        "sellSizeUom", "sellSizeQuantity", "sellSizeText",
        "consumerUnitOfMeasure", "consumerSize", "productSizeDescription",
    ]
    complex_hits = []
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
                complex_hits.append({"field": f, "type": inner})
                print(f"  ⊞ SKUs.{f:32s} → complex type={inner}")
            else:
                print(f"  ? SKUs.{f:32s} {em[:80]}")
        else:
            skus = (body.get("data") or {}).get("getProductById", {}).get("SKUs") or []
            val = skus[0].get(f) if skus else None
            marker = " ★" if val and "12" in str(val) else ""
            print(f"  ✓ SKUs.{f:32s} → {json.dumps(val)[:80]}{marker}")

    # ============================================================
    # 2) Explore complex fields
    # ============================================================
    if complex_hits:
        section("2) Explore complex SKU fields")
        sub_guesses = ["value", "amount", "quantity", "display", "text",
                       "formatted", "label", "displayValue", "unit",
                       "unitOfMeasure", "size"]
        for c_hit in complex_hits:
            fname = c_hit["field"]
            print(f"\n  --- SKUs.{fname} (type {c_hit['type']}) ---")
            for sub in sub_guesses:
                q = f'query Q($id: String!, $s: String) {{ getProductById(id: $id, storeId: $s) {{ SKUs {{ {fname} {{ {sub} }} }} }} }}'
                r = post(c, q, {"id": TEST_ID, "s": TARGET})
                body = r.json()
                if "errors" in body and body["errors"]:
                    em = body["errors"][0]["message"]
                    if "Cannot query field" in em:
                        continue
                    else:
                        print(f"    ? {sub}: {em[:70]}")
                else:
                    skus = (body.get("data") or {}).get("getProductById", {}).get("SKUs") or []
                    v = skus[0].get(fname) if skus else None
                    marker = " ★" if v and "12" in str(v) else ""
                    print(f"    ✓ {sub}: {json.dumps(v)[:120]}{marker}")

    # ============================================================
    # 3) Full response dump + productDescription regex
    # ============================================================
    section("3) Product __typename + productDescription pattern search")
    full_q = """
    query Q($id: String!, $s: String) {
      getProductById(id: $id, storeId: $s) {
        __typename
        id
        fullDisplayName
        productDescription
        SKUs {
          __typename
          id
          unitOfMeasure
        }
      }
    }
    """
    r = post(c, full_q, {"id": TEST_ID, "s": TARGET})
    body = r.json()
    prod = (body.get("data") or {}).get("getProductById") or {}
    print(f"  Product __typename: {prod.get('__typename')}")
    if prod.get("SKUs"):
        print(f"  SKU __typename:     {prod['SKUs'][0].get('__typename')}")
    desc = prod.get("productDescription") or ""
    if desc:
        print(f"  productDescription: {desc[:250]}")
        size_matches = re.findall(r'(\d+(?:\.\d+)?)\s*(oz|OZ|lb|LB|ct|CT|g|kg|ml|fl oz|piece|count|pk)\b', desc, re.I)
        print(f"  size patterns: {size_matches[:15]}")

    # ============================================================
    # 4) Schema introspection on SKU-type
    # ============================================================
    section("4) Schema introspection on SKU type")
    for typename in ["SKU", "Sku", "SKUV2", "ProductSKU", "SkuType"]:
        q = f'query {{ __type(name: "{typename}") {{ name fields {{ name type {{ name kind ofType {{ name kind }} }} }} }} }}'
        r = post(c, q)
        body = r.json()
        if "errors" not in body:
            t = body.get("data", {}).get("__type")
            if t and t.get("fields"):
                print(f"  Type: {t['name']} ({len(t['fields'])} fields)")
                for fld in t["fields"]:
                    tn = (fld.get("type") or {}).get("name")
                    if not tn:
                        tn = (fld.get("type") or {}).get("ofType", {}).get("name", "?")
                    print(f"    {fld['name']:35s} {tn}")
                break
    else:
        print("  introspection appears blocked or SKU type not found")

    # ============================================================
    # 5) HTML fetch — page structure
    # ============================================================
    section("5) HTML fetch — page structure analysis")
    slug = "cafe-ol-by-h-e-b-texas-pecan-medium-roast-ground-coffee"
    url = f"https://www.heb.com/product-detail/{slug}/{TEST_ID}"
    r = c.get(url)
    print(f"  GET {url}")
    print(f"  status={r.status_code}, size={len(r.text):,}")

    for pat in [r'>\s*12\s*oz\s*<', r'"12 oz"', r'12 OZ', r'12oz',
                r'"12"', r'unitOfMeasureAmount', r'sellSize',
                r'netContent', r'packageSize']:
        matches = list(re.finditer(pat, r.text, re.I))
        if matches:
            print(f"\n  pattern '{pat}': {len(matches)} match(es)")
            for m in matches[:2]:
                ctx = r.text[max(0, m.start()-80):m.end()+80].replace("\n", " ")
                print(f"    …{ctx}…")

    for id_pat in [r'id="__NEXT_DATA__"', r'id="__NUXT_DATA__"',
                   r'id="__INITIAL_STATE__"', r'window\.__NEXT_DATA__',
                   r'window\.__NUXT__', r'window\.__STATE__',
                   r'window\.__data__', r'__APOLLO_STATE__']:
        if re.search(id_pat, r.text):
            print(f"\n  found data blob pattern: {id_pat}")


if __name__ == "__main__":
    main()
