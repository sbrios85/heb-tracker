"""
Probe v3: getProductById is the answer (works with explicit storeId).

Phase 2 finding: getProductById(id: String!, storeId: String) returns a
Product and accepts an explicit storeId — so we can request store 57
directly without fighting the product-page store binding (which is tied
to a logged-in User account).

Phase 3: discover getProductById's full field set with storeId=57.
We need: price, image, SKUs, inventory, availability, description, etc.
We reuse the field names we already KNOW exist on the product blob from
__NEXT_DATA__ (fullDisplayName, SKUs, productImageUrls, etc.) plus probe more.
"""

import json
import re
import sys
import time
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import HEADERS, HOMEPAGE, GRAPHQL_ENDPOINT

TEST_PRODUCT_ID = "583162"
TARGET = "57"


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
    # A) Probe getProductById field set (fixed logic: no error = valid)
    # ============================================================
    section("A) getProductById field probe with storeId=57")
    # These are the field names we KNOW exist from the __NEXT_DATA__ product blob
    known_fields = [
        "id", "fullDisplayName", "productDescription", "productPageURL",
        "brand", "breadcrumbs", "coupons", "carouselImageUrls",
        "inAssortment", "inventory", "productImageUrls", "thumbnailImageUrls",
        "productLocation", "SKUs", "isEbtSnapProduct", "onAd", "isNew",
        "nutritionLabels", "ingredientStatement", "lifestyles",
        "minimumOrderQuantity", "maximumOrderQuantity", "safetyWarning",
        "preparationInstructions", "showCouponFlag", "store",
        # extra guesses
        "displayName", "name", "price", "availability",
    ]
    valid_scalar, valid_complex, unknown = [], [], []
    for f in known_fields:
        q = f'query Q($id: String!, $s: String) {{ getProductById(id: $id, storeId: $s) {{ {f} }} }}'
        r = post(c, q, {"id": TEST_PRODUCT_ID, "s": TARGET})
        try:
            body = r.json()
        except Exception:
            continue
        if "errors" in body and body["errors"]:
            em = body["errors"][0]["message"]
            if "Cannot query field" in em:
                unknown.append(f)
            elif "must have a selection of subfields" in em:
                m = re.search(r"of type [\"']([\w!\[\]]+)[\"']", em)
                inner = m.group(1) if m else "?"
                valid_complex.append({"field": f, "type": inner})
                print(f"  ⊞ {f:25s} type={inner}")
            else:
                print(f"  ? {f:25s} {em[:120]}")
        else:
            valid_scalar.append(f)
            pd = (body.get("data") or {}).get("getProductById") or {}
            v = pd.get(f)
            print(f"  ✓ {f:25s} -> {json.dumps(v)[:110] if v is not None else 'null'}")
        time.sleep(0.15)

    print(f"\n  scalar={len(valid_scalar)} complex={len(valid_complex)} unknown={len(unknown)}")

    # ============================================================
    # B) Full fetch: pull everything we need in one query with storeId=57
    # ============================================================
    section("B) Full getProductById with SKUs/prices/images — storeId=57")
    full_q = """
    query Q($id: String!, $s: String) {
      getProductById(id: $id, storeId: $s) {
        id
        fullDisplayName
        productPageURL
        brand { name isOwnBrand }
        inAssortment
        inventory { inventoryState }
        productImageUrls { url size }
        productLocation { location availability }
        SKUs {
          id
          contextPrices {
            context
            isOnSale
            listPrice { amount formattedAmount unit }
            salePrice { amount formattedAmount unit }
            unitListPrice { amount formattedAmount unit }
          }
        }
      }
    }
    """
    r = post(c, full_q, {"id": TEST_PRODUCT_ID, "s": TARGET})
    print(f"  status: {r.status_code}")
    try:
        body = r.json()
        print(json.dumps(body, indent=2)[:3500])
    except Exception:
        print(r.text[:2000])

    # ============================================================
    # C) Compare: same query with storeId=92 (Victoria) to confirm prices differ
    # ============================================================
    section("C) Compare store 57 vs 92 — confirm storeId actually changes data")
    for sid in ["57", "92"]:
        r = post(c, full_q, {"id": TEST_PRODUCT_ID, "s": sid})
        try:
            body = r.json()
            prod = (body.get("data") or {}).get("getProductById") or {}
            skus = prod.get("SKUs") or []
            online = None
            if skus:
                for cp in (skus[0].get("contextPrices") or []):
                    if cp.get("context") == "ONLINE":
                        online = (cp.get("listPrice") or {}).get("formattedAmount")
            inv = (prod.get("inventory") or {}).get("inventoryState")
            loc = (prod.get("productLocation") or {}).get("location")
            print(f"  storeId={sid}: online_price={online} inventory={inv} aisle={loc}")
        except Exception as e:
            print(f"  storeId={sid}: error {e}")
        time.sleep(0.3)

    # ============================================================
    # D) Does getProductById require storeId, or default if omitted?
    # ============================================================
    section("D) getProductById without storeId")
    q = 'query Q($id: String!) { getProductById(id: $id) { id store { storeNumber } } }'
    r = post(c, q, {"id": TEST_PRODUCT_ID})
    print(f"  {r.text[:300]}")


if __name__ == "__main__":
    main()
