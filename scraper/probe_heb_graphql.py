"""
H-E-B GraphQL Probe — Phase 10
------------------------------
Phase 9 confirmed:
  - filter syntax: "brand:<exact brand name>" (others silently ignored)
  - SearchSortBy enum: only POPULARITY is valid
  - Product has fields: brand (Brand), availability (Availability!),
    inventory (Inventory), breadcrumbs ([CategoryBreadCrumb!]),
    coupons ([CouponV2!])
  - Server returns total=1170 (everything) when filter is malformed

Phase 10 strategy:
  - Stop guessing scalar field names — my path-walker had a bug where
    fields that returned null were classified as "other" instead of valid.
  - Re-probe Product, CategoryBreadCrumb, Inventory, Availability with
    fixed logic: if the response has no "errors" and status is 200,
    the field IS valid regardless of value.
  - Then assemble a working full query with all valid scalars.
"""

import json
import re
import time
from pathlib import Path

import httpx

ENDPOINT = "https://www.heb.com/graphql"
HOMEPAGE = "https://www.heb.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.heb.com",
    "Referer": "https://www.heb.com/",
}
OUTDIR = Path(__file__).parent.parent / "probe_output"
OUTDIR.mkdir(exist_ok=True)


def safe(s):
    return re.sub(r'[^A-Za-z0-9_\-.]', '_', str(s))[:80]


def save(name, data):
    p = OUTDIR / safe(name)
    p.write_text(json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data))
    return p


def section(t):
    print(f"\n{'=' * 70}\n  {t}\n{'=' * 70}")


def post(c, q, v=None):
    payload = {"query": q}
    if v is not None:
        payload["variables"] = v
    return c.post(ENDPOINT, json=payload)


def probe_fields_fixed(client, query_template, candidates, type_label):
    """FIXED probe: a field is VALID if no errors come back, regardless of
    whether the value is null. Complex fields cause 'must have subfields'
    error and are tracked separately."""
    valid_scalar = []   # field names known to work (value may be null)
    valid_complex = []  # field name + inner type
    unknown = []        # field doesn't exist
    other = []
    for f in candidates:
        q = query_template.replace("__FIELD__", f)
        try:
            r = post(client, q)
            body = r.json() if r.text.startswith("{") else None
        except Exception:
            continue
        if not body:
            continue
        if "errors" in body and body["errors"]:
            em = body["errors"][0]["message"]
            if "Cannot query field" in em:
                unknown.append(f)
            elif "must have a selection of subfields" in em:
                m = re.search(r"of type [\"']([\w!\[\]]+)[\"']", em)
                inner = m.group(1) if m else "?"
                valid_complex.append({"field": f, "type": inner})
                print(f"  ⊞ {f:25s} NEEDS_SUBFIELDS type={inner}")
            else:
                other.append({"field": f, "err": em[:200]})
                print(f"  ? {f:25s} {em[:200]}")
        else:
            # No errors at all = field is valid, even if value is null
            valid_scalar.append(f)
            # Try to extract the value for display
            data = body.get("data")
            print(f"  ✓ {f:25s} valid (response: {json.dumps(data)[:140] if data else '<no data>'})")
        time.sleep(0.10)
    print(f"\n  [{type_label}] scalar={len(valid_scalar)} complex={len(valid_complex)} unknown={len(unknown)} other={len(other)}")
    return {"scalar": valid_scalar, "complex": valid_complex, "unknown": unknown, "other": other}


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        section("Setup")
        c.get(HOMEPAGE)

        product_candidates = [
            "id", "productId", "sku", "upc", "ean", "productNumber",
            "primaryProductId", "productGroupId", "code",
            "name", "displayName", "title", "productName", "label",
            "brandName", "manufacturer",
            "price", "pricing", "prices", "regularPrice", "salePrice",
            "currentPrice", "listPrice", "unitPrice", "perUnitPrice",
            "displayPrice", "displayedUnitPrice",
            "image", "images", "imageUrl", "imageUrls", "primaryImage",
            "thumbnail", "media",
            "description", "longDescription", "shortDescription", "details",
            "available", "inStock", "isAvailable",
            "inventoryState", "stockStatus",
            "isInStock", "outOfStock", "inAssortment",
            "isDiscontinued", "isActive",
            "category", "categories", "department", "taxonomy",
            "taxonomyPath", "categoryId",
            "size", "weight", "uom", "unitOfMeasure", "packaging",
            "packageSize", "containerSize",
            "url", "slug", "productUrl", "path", "permalink",
            "ingredients", "nutrition",
            "averageRating", "reviewCount",
            "tags", "labels", "attributes",
            "isPrivateLabel", "ownedBrand", "isHebBrand",
            "aisle", "aisleLocation",
            "snap", "snapEligible", "isSnapEligible", "isSnapAble",
            "couponInfo", "promotions", "savings",
            "fulfillmentChannels",
        ]

        # =========================================================
        # A) Product fields via records[0] — FIXED logic
        # =========================================================
        section("A) Product fields via productSearch.records (FIXED probe)")
        template = '''query Q {
          productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", filter: "brand:CAFE Olé by H-E-B", limit: 1) {
            records { __FIELD__ }
          }
        }'''
        result_product = probe_fields_fixed(c, template, product_candidates, "Product")
        save("A_Product_fixed.json", result_product)

        # =========================================================
        # B) CategoryBreadCrumb subfields (FIXED probe)
        # =========================================================
        section("B) CategoryBreadCrumb subfields (FIXED probe)")
        cbc_candidates = [
            "id", "categoryId", "name", "displayName", "label",
            "slug", "url", "path", "depth", "level",
            "parentId", "parentCategoryId",
            "title", "text",
        ]
        cbc_template = '''query Q {
          browseCategory(storeId: 57, shoppingContext: CURBSIDE_PICKUP, categoryId: "490086") {
            breadcrumbs { __FIELD__ }
          }
        }'''
        result_cbc = probe_fields_fixed(c, cbc_template, cbc_candidates, "CategoryBreadCrumb")
        save("B_CBC_fixed.json", result_cbc)

        # =========================================================
        # C) Availability and Inventory subfields
        # =========================================================
        section("C) Availability subfields")
        av_candidates = [
            "status", "state", "available", "isAvailable", "inStock",
            "outOfStock", "level", "code", "type", "kind",
            "channels", "fulfillmentChannels", "channel",
            "curbside", "delivery", "pickup", "shipping",
            "message", "displayMessage", "label",
        ]
        av_template = '''query Q {
          productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", filter: "brand:CAFE Olé by H-E-B", limit: 1) {
            records { availability { __FIELD__ } }
          }
        }'''
        result_av = probe_fields_fixed(c, av_template, av_candidates, "Availability")
        save("C_Availability.json", result_av)

        section("C2) Inventory subfields")
        inv_candidates = [
            "state", "inventoryState", "status", "available", "level",
            "stockLevel", "quantity", "count",
            "inStock", "outOfStock", "displayMessage",
            "lastUpdated", "updatedAt",
        ]
        inv_template = '''query Q {
          productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", filter: "brand:CAFE Olé by H-E-B", limit: 1) {
            records { inventory { __FIELD__ } }
          }
        }'''
        result_inv = probe_fields_fixed(c, inv_template, inv_candidates, "Inventory")
        save("C2_Inventory.json", result_inv)

        # =========================================================
        # D) Big query: pull 3 real products with EVERYTHING valid
        # =========================================================
        section("D) Full product dump — 3 CAFE Olé products")
        scalars = result_product["scalar"]
        # Expand complex fields with our discovered subfields
        complex_expansions = ["brand { name }"]
        if result_av["scalar"]:
            av_keys = " ".join(result_av["scalar"])
            complex_expansions.append(f"availability {{ {av_keys} }}")
        else:
            complex_expansions.append("availability { __typename }")
        if result_inv["scalar"]:
            inv_keys = " ".join(result_inv["scalar"])
            complex_expansions.append(f"inventory {{ {inv_keys} }}")
        else:
            complex_expansions.append("inventory { __typename }")
        if result_cbc["scalar"]:
            cbc_keys = " ".join(result_cbc["scalar"])
            complex_expansions.append(f"breadcrumbs {{ {cbc_keys} }}")

        scalar_part = "\n              ".join(scalars)
        complex_part = "\n              ".join(complex_expansions)
        qbig = f'''query Q {{
          productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", filter: "brand:CAFE Olé by H-E-B", limit: 3) {{
            total
            records {{
              __typename
              {scalar_part}
              {complex_part}
            }}
          }}
        }}'''
        print("  Query being sent:")
        print(qbig)
        r = post(c, qbig)
        print(f"\n  status: {r.status_code}")
        try:
            body = r.json()
            print(f"  body (truncated):\n{json.dumps(body, indent=2)[:8000]}")
        except Exception:
            print(f"  raw: {r.text[:5000]}")
        save("D_FULL_DUMP.json", r.text)

        # =========================================================
        # E) Brand-filter exploration: list all H-E-B owned brands
        # =========================================================
        section("E) Probe known H-E-B brand names")
        # We saw "CAFE Olé by H-E-B" gives 188 results, "H-E-B" gives 35.
        # Try other house brands.
        brands_to_try = [
            "H-E-B",
            "Central Market",
            "Central Market Organics",
            "Hill Country Fair",
            "H-E-B Select Ingredients",
            "CAFE Olé by H-E-B",
            "CAFE Olé Organics by H-E-B",
            "H-E-B Bakery",
            "H-E-B Texas Style",
            "Primo Picks",
            "Texas Tough",
            "Mi Tienda",
            "Hill Country Essentials",
            "H-E-B Organics",
            "H-E-B Pet",
            "Field & Future by H-E-B",
            "Creamy Creations",
            "Country Store by H-E-B",
        ]
        brand_results = {}
        for b in brands_to_try:
            q = f'''query Q {{
              productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "", filter: {json.dumps("brand:" + b)}, limit: 1) {{
                total
                records {{ brand {{ name }} }}
              }}
            }}'''
            r = post(c, q)
            try:
                body = r.json()
                if "errors" not in body:
                    total = (body.get("data") or {}).get("productSearch", {}).get("total")
                    records = (body.get("data") or {}).get("productSearch", {}).get("records") or []
                    actual_brand = ""
                    if records:
                        actual_brand = (records[0].get("brand") or {}).get("name", "?")
                    brand_results[b] = {"total": total, "actual_brand": actual_brand}
                    matched = "✓" if actual_brand == b else "—" if total == 1170 else "≠"
                    print(f"  {matched} brand:{b:38s} total={total:5d}  sample={actual_brand[:40]}")
                else:
                    em = body["errors"][0]["message"]
                    print(f"  ✗ brand:{b:38s} ERR: {em[:160]}")
            except Exception as e:
                print(f"  ! brand:{b:38s} {e}")
            time.sleep(0.3)
        save("E_brand_results.json", brand_results)

        # =========================================================
        # F) productSearch with empty query — can we list ALL products?
        # =========================================================
        section("F) productSearch with empty query")
        # If we can pass query: "" and get all products, we can paginate
        # everything. We've seen empty query work above; check totals.
        q = '''query Q {
          productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "", limit: 1) {
            total
            records { brand { name } }
          }
        }'''
        r = post(c, q)
        print(f"  empty query: {r.text[:400]}")
        save("F_empty_query.json", r.text)

    print(f"\nDone. Output in {OUTDIR}")


if __name__ == "__main__":
    main()
