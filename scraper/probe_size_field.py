"""
Probe v3: pinpoint the "12 oz" quantity.

Findings so far:
  - SKUs.unitOfMeasure = "OUNCE"
  - SKUs.unitOfMeasureQuantity = 1  <-- but coffee is 12 oz, so this isn't it
  - Product __typename = "Product", SKU __typename = "SKU"

The size "12" must be on Product (not SKU), or in a field we haven't tried.
This probe:
  1. Test candidate scalar fields on Product (aggressive expansion)
  2. Retest the productMeasure/measures paths on Product that returned complex
  3. Try nested SKU fields like sellSize, sellPrice.priceType, etc.
  4. Look for the number 12 anywhere in a huge dump of the response
"""

import json
import re
import sys
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import HEADERS, HOMEPAGE, GRAPHQL_ENDPOINT, WALDRON_STORE_NUMBER

TEST_ID = "583162"     # coffee, expected: 12 oz
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
    # 1) Product-level scalar candidates
    # ============================================================
    section("1) Product-level scalar-field probe")
    prod_candidates = [
        "productSize", "productSizeText", "productSizeLabel",
        "displaySize", "displaySizeText", "displaySizeLabel",
        "size", "sizeText", "sizeLabel", "sizeDisplay",
        "netContent", "netContentAmount", "netContentDisplay", "netContentValue", "netContentText",
        "packageSize", "packageSizeAmount", "packageSizeText",
        "quantity", "quantityText", "quantityDisplay",
        "weight", "weightAmount", "weightDisplay", "weightText",
        "netWeight", "netWeightAmount", "netWeightText",
        "measure", "measureAmount", "measureDisplay", "measureText",
        "unitSize", "unitSizeAmount", "unitSizeText",
        "productWeight", "productWeightAmount",
        "sellSize", "sellSizeText", "sellSizeAmount",
        "consumerUnit", "consumerUnitSize", "consumerUnitOfMeasure",
        "productLabel", "productLabelText",
    ]
    complex_hits_prod = []
    for f in prod_candidates:
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
                complex_hits_prod.append({"field": f, "type": inner})
                print(f"  ⊞ {f:30s} → complex type={inner}")
            else:
                print(f"  ? {f:30s} {em[:80]}")
        else:
            val = (body.get("data") or {}).get("getProductById", {}).get(f)
            marker = " ★" if val and "12" in str(val) else ""
            print(f"  ✓ {f:30s} → {json.dumps(val)[:100]}{marker}")

    # ============================================================
    # 2) Explore complex Product fields
    # ============================================================
    if complex_hits_prod:
        section("2) Explore complex Product fields")
        sub_guesses = ["value", "amount", "quantity", "displayValue", "display",
                       "text", "unit", "unitOfMeasure", "label", "size",
                       "formattedValue", "formatted"]
        for h in complex_hits_prod:
            print(f"\n  --- {h['field']} (type {h['type']}) ---")
            for sub in sub_guesses:
                q = f'query Q($id: String!, $s: String) {{ getProductById(id: $id, storeId: $s) {{ {h["field"]} {{ {sub} }} }} }}'
                r = post(c, q, {"id": TEST_ID, "s": TARGET})
                body = r.json()
                if "errors" in body and body["errors"]:
                    em = body["errors"][0]["message"]
                    if "Cannot query field" in em:
                        continue
                    else:
                        print(f"    ? {sub}: {em[:70]}")
                else:
                    v = (body.get("data") or {}).get("getProductById", {}).get(h["field"])
                    marker = " ★" if v and "12" in str(v) else ""
                    print(f"    ✓ {sub}: {json.dumps(v)[:120]}{marker}")

    # ============================================================
    # 3) Test more SKU fields (things I missed the first time)
    # ============================================================
    section("3) Additional SKU fields")
    sku_more = [
        "productSize", "productSizeAmount", "productSizeText",
        "displaySize", "displaySizeText",
        "sellSize", "sellSizeAmount", "sellSizeText",
        "netContent", "netContentAmount", "netContentText",
        "packageSize", "packageWeight",
        "quantity", "weight", "size",
        "sellingUnitOfMeasureAmount", "sellingUnitOfMeasureQty",
        "sellingUnitCount", "sellingUnitSize",
        "unitOfMeasureAmount", "unitOfMeasureValue",
        "displayUnitOfMeasureAmount",
        "shelfSize", "shelfLabel",
    ]
    for f in sku_more:
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
                print(f"  ⊞ SKUs.{f:32s} → complex type={inner}")
            else:
                print(f"  ? SKUs.{f:32s} {em[:80]}")
        else:
            skus = (body.get("data") or {}).get("getProductById", {}).get("SKUs") or []
            val = skus[0].get(f) if skus else None
            marker = " ★" if val and "12" in str(val) else ""
            print(f"  ✓ SKUs.{f:32s} → {json.dumps(val)[:100]}{marker}")

    # ============================================================
    # 4) Verify unitOfMeasureQuantity across products of KNOWN size
    #    Coffee (583162)      -> should be 12 oz
    #    Hydrocortisone (1403504) -> should be 1 oz
    #    Some 6-pack cans (search for one)
    # ============================================================
    section("4) unitOfMeasureQuantity/unitOfMeasure across known-size products")
    test_products = [
        ("583162",  "coffee, expected 12 oz"),
        ("1403504", "hydrocortisone, expected 1 oz"),
    ]
    q = """
    query Q($id: String!, $s: String) {
      getProductById(id: $id, storeId: $s) {
        fullDisplayName
        productDescription
        SKUs {
          unitOfMeasure
          unitOfMeasureQuantity
        }
      }
    }
    """
    for pid, note in test_products:
        r = post(c, q, {"id": pid, "s": TARGET})
        body = r.json()
        p = (body.get("data") or {}).get("getProductById") or {}
        skus = p.get("SKUs") or [{}]
        s0 = skus[0]
        name = p.get("fullDisplayName", "")[:70]
        print(f"  {pid} ({note})")
        print(f"    fullDisplayName: {name}")
        print(f"    unitOfMeasure = {s0.get('unitOfMeasure')}")
        print(f"    unitOfMeasureQuantity = {s0.get('unitOfMeasureQuantity')}")
        # And check description for a size
        desc = p.get("productDescription") or ""
        matches = re.findall(r'(\d+(?:\.\d+)?)\s*(oz|ct|lb|fl oz|count|pk|piece)\b', desc, re.I)
        print(f"    description size matches: {matches[:5]}")


if __name__ == "__main__":
    main()
