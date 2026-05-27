"""
H-E-B GraphQL Probe — Phase 9
-----------------------------
Phase 8 confirmed:
  - productSearch(storeId: Int!, shoppingContext, query: String!, limit, offset, filter, sortBy) -> ProductCollection
  - browseCategory(storeId, shoppingContext, categoryId: String!) -> BrowseProductCollection
  - Both collections have: records: [Product]!, total: Int, filters: ProductSearchFilters
  - BrowseProductCollection also has: breadcrumbs: [CategoryBreadCrumb]!
  - Product type has at least `brand: Brand` field (works via records[] path)
  - filter takes a String, sortBy is an enum "SearchSortBy"

Phase 9 goals:
  A) Probe Product fields via the records[] path (it works there).
  B) Probe subfields of Brand, ProductSearchFilters, CategoryBreadCrumb.
  C) Discover filter string syntax (brand filtering).
  D) Discover SearchSortBy enum values.
  E) Pull a real ProductCollection.records[] dump with all fields populated.
  F) Try same approach on productDetail — maybe it works after warm-up.
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
CTX = "CURBSIDE_PICKUP"
STORE = 57
SKU = "1510154"


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


def err_msg(r):
    try:
        return r.json()["errors"][0]["message"]
    except Exception:
        return r.text[:200]


def probe_field_at_path(client, template, path, candidates, label):
    """Probe each candidate by substituting __FIELD__ in template, then walking
    the JSON path to extract the value if no error."""
    valid_scalar = {}
    valid_complex = []
    unknown = []
    other = []
    for f in candidates:
        q = template.replace("__FIELD__", f)
        try:
            r = post(client, q)
            body = r.json() if r.text.startswith("{") else None
        except Exception:
            continue
        if not body:
            continue
        if "errors" in body:
            em = body["errors"][0]["message"]
            if "Cannot query field" in em:
                unknown.append(f)
            elif "must have a selection of subfields" in em:
                m = re.search(r"of type [\"']([\w!\[\]]+)[\"']", em)
                inner = m.group(1) if m else "?"
                valid_complex.append({"field": f, "type": inner})
                print(f"  ⊞ {f:25s} NEEDS_SUBFIELDS type={inner}")
            else:
                other.append((f, em[:200]))
                print(f"  ? {f:25s} {em[:200]}")
        else:
            cur = body.get("data") or {}
            for key in path:
                if isinstance(cur, list) and cur:
                    cur = cur[0]
                if isinstance(cur, dict):
                    cur = cur.get(key)
            # cur should now be at the record level
            if isinstance(cur, dict) and f in cur:
                v = cur[f]
                valid_scalar[f] = v
                print(f"  ✓ {f:25s} -> {json.dumps(v)[:140] if v is not None else 'null'}")
            else:
                other.append((f, f"no_field_in_path: cur_type={type(cur).__name__}"))
        time.sleep(0.12)
    print(f"\n  [{label}] scalar={len(valid_scalar)} complex={len(valid_complex)} unknown={len(unknown)} other={len(other)}")
    return {"scalar": valid_scalar, "complex": valid_complex, "unknown": unknown, "other": other}


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        section("Setup")
        c.get(HOMEPAGE)

        # =========================================================
        # A) Probe Product fields via records[] path
        # =========================================================
        section("A) Product fields via productSearch.records[0]")
        # Use limit:1 to keep responses small
        product_field_template = '''query Q {
          productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", limit: 1) {
            records { __FIELD__ }
          }
        }'''
        product_candidates = [
            # IDs
            "id", "productId", "sku", "upc", "ean", "productNumber",
            "primaryProductId", "productGroupId", "code",
            # Names
            "name", "displayName", "title", "productName", "label",
            # Brand (we already know this exists as complex)
            "brand", "brandName", "manufacturer",
            # Pricing
            "price", "pricing", "prices", "regularPrice", "salePrice",
            "currentPrice", "listPrice", "unitPrice", "perUnitPrice",
            "displayPrice", "displayedUnitPrice",
            # Images
            "image", "images", "imageUrl", "imageUrls", "primaryImage",
            "thumbnail", "media",
            # Description
            "description", "longDescription", "shortDescription", "details",
            # Availability
            "available", "availability", "inStock", "isAvailable",
            "inventory", "inventoryState", "stockStatus",
            "isInStock", "outOfStock", "inAssortment",
            "isDiscontinued", "isActive",
            # Categories
            "category", "categories", "department", "taxonomy",
            "taxonomyPath", "breadcrumbs", "categoryId",
            # Size
            "size", "weight", "uom", "unitOfMeasure", "packaging",
            "packageSize", "containerSize",
            # URL
            "url", "slug", "productUrl", "path",
            # Other
            "ingredients", "nutrition",
            "averageRating", "reviewCount",
            "tags", "labels", "attributes",
            "isPrivateLabel", "ownedBrand", "isHebBrand",
            "aisle", "aisleLocation",
            "snap", "snapEligible", "isSnapEligible",
            "couponInfo", "coupons", "promotions", "savings",
            "fulfillmentChannels",
        ]
        result_A = probe_field_at_path(
            c, product_field_template,
            ["productSearch", "records"],
            product_candidates, "Product"
        )
        save("A_Product_fields.json", result_A)

        # =========================================================
        # B) Subfields of complex types (Brand, ProductSearchFilters, CategoryBreadCrumb)
        # =========================================================
        section("B) Subfields of complex types")

        # Brand
        print("\n  --- Brand subfields ---")
        brand_subfields = [
            "id", "name", "displayName", "slug",
            "isHeb", "isHebBrand", "isOwnedBrand", "isPrivateLabel",
            "logo", "logoUrl", "image", "imageUrl",
            "description", "url", "categoryId",
        ]
        brand_template = '''query Q {
          productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", limit: 1) {
            records { brand { __FIELD__ } }
          }
        }'''
        result_B_brand = probe_field_at_path(
            c, brand_template,
            ["productSearch", "records", "brand"],
            brand_subfields, "Brand"
        )
        save("B_Brand.json", result_B_brand)

        # ProductSearchFilters — for brand filter syntax discovery!
        print("\n  --- ProductSearchFilters subfields ---")
        psf_subfields = [
            "brands", "brand", "categories", "category",
            "prices", "price", "priceRange",
            "departments", "department",
            "dietary", "dietaryAttributes",
            "options", "values", "items",
            "facets", "filters",
            "available", "isAvailable",
            "type", "kind",
            "name", "id", "label", "displayName",
            "count", "total",
        ]
        psf_template = '''query Q {
          productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", limit: 1) {
            filters { __FIELD__ }
          }
        }'''
        result_B_psf = probe_field_at_path(
            c, psf_template,
            ["productSearch", "filters"],
            psf_subfields, "ProductSearchFilters"
        )
        save("B_ProductSearchFilters.json", result_B_psf)

        # CategoryBreadCrumb — to navigate the category tree
        print("\n  --- CategoryBreadCrumb subfields ---")
        cbc_subfields = [
            "id", "categoryId", "name", "displayName", "label",
            "slug", "url", "path", "depth", "level",
            "parentId", "parentCategoryId",
        ]
        cbc_template = '''query Q {
          browseCategory(storeId: 57, shoppingContext: CURBSIDE_PICKUP, categoryId: "490086") {
            breadcrumbs { __FIELD__ }
          }
        }'''
        result_B_cbc = probe_field_at_path(
            c, cbc_template,
            ["browseCategory", "breadcrumbs"],
            cbc_subfields, "CategoryBreadCrumb"
        )
        save("B_CategoryBreadCrumb.json", result_B_cbc)

        # =========================================================
        # C) filter string syntax discovery
        # =========================================================
        section("C) filter argument: discover brand-filter syntax")
        # The filter arg takes a String. We need to find what format encodes
        # a brand filter. Common conventions:
        filter_attempts = [
            "brand:CAFE Olé by H-E-B",
            "brand=CAFE Olé by H-E-B",
            'brand:"CAFE Olé by H-E-B"',
            "brandName:CAFE Olé by H-E-B",
            "brand:central-market",
            "brand:H-E-B",
            "isOwnedBrand:true",
            "isHebBrand:true",
            "ownedBrand",
            "private_label",
        ]
        for fv in filter_attempts:
            q = f'''query Q {{
              productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", filter: {json.dumps(fv)}, limit: 1) {{
                total
                records {{ brand {{ name }} }}
              }}
            }}'''
            r = post(c, q)
            try:
                body = r.json()
                if "errors" in body:
                    em = body["errors"][0]["message"]
                    print(f"  {fv:45s} ERR: {em[:200]}")
                else:
                    total = (body.get("data") or {}).get("productSearch", {}).get("total")
                    records = (body.get("data") or {}).get("productSearch", {}).get("records") or []
                    sample_brand = ""
                    if records:
                        sample_brand = (records[0].get("brand") or {}).get("name", "?")
                    print(f"  {fv:45s} total={total} sample_brand={sample_brand}")
                    save(f"C_filter_{safe(fv)}.json", body)
            except Exception:
                pass
            time.sleep(0.3)

        # =========================================================
        # D) SearchSortBy enum values
        # =========================================================
        section("D) SearchSortBy enum values")
        # First, throw a garbage value to learn what's accepted
        q = 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", sortBy: __INVALID__, limit: 1) { total } }'
        r = post(c, q)
        print(f"  garbage probe: {r.text[:600]}")
        save("D_sortby_garbage.json", r.text)

        sort_candidates = [
            "RELEVANCE", "BEST_MATCH", "PRICE_ASC", "PRICE_DESC",
            "PRICE_LOW_TO_HIGH", "PRICE_HIGH_TO_LOW",
            "NAME_ASC", "NAME_DESC", "ALPHABETICAL",
            "RATING", "POPULARITY", "TRENDING",
            "NEWEST", "OLDEST",
        ]
        valid_sorts = []
        for s in sort_candidates:
            q = f'query Q {{ productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", sortBy: {s}, limit: 1) {{ total }} }}'
            r = post(c, q)
            try:
                body = r.json()
                if "errors" not in body:
                    valid_sorts.append(s)
                    print(f"  ✓ {s}")
                else:
                    em = body["errors"][0]["message"]
                    if "does not exist in" in em:
                        pass  # invalid enum value
                    else:
                        print(f"  ? {s:25s} {em[:180]}")
            except Exception:
                pass
            time.sleep(0.2)
        print(f"\n  Valid sort values: {valid_sorts}")
        save("D_valid_sorts.json", valid_sorts)

        # =========================================================
        # E) Full ProductCollection dump with all valid scalar fields
        # =========================================================
        section("E) Full productSearch with all valid Product scalars + brand subfields")
        scalar_keys = list(result_A["scalar"].keys())
        brand_keys = list(result_B_brand["scalar"].keys())
        scalar_part = "\n            ".join(scalar_keys)
        brand_part = "\n              ".join(brand_keys)
        complex_to_include = []
        # Expand select complex fields by guessing subfields
        # For "image"/"primaryImage" -> {url}
        for c_field in result_A["complex"]:
            f = c_field["field"]
            t = c_field["type"]
            if "Image" in t or "image" in f.lower():
                complex_to_include.append(f"{f} {{ url }}")
            elif "Price" in t or "price" in f.lower():
                complex_to_include.append(f"{f} {{ amount currency }}")
            elif "Category" in t or "category" in f.lower():
                complex_to_include.append(f"{f} {{ name }}")

        complex_part = "\n            ".join(complex_to_include)
        qbig = f'''query Q {{
          productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", limit: 5) {{
            total
            records {{
              __typename
              {scalar_part}
              {('brand { ' + brand_part + ' }') if brand_keys else 'brand { __typename }'}
              {complex_part}
            }}
          }}
        }}'''
        r = post(c, qbig)
        print(f"  status: {r.status_code}")
        # Pretty-print the response
        try:
            body = r.json()
            print(f"  body:\n{json.dumps(body, indent=2)[:5000]}")
        except Exception:
            print(f"  raw body (first 4000): {r.text[:4000]}")
        save("E_FULL_DUMP.json", r.text)

        # =========================================================
        # F) Now retry productDetail with the SAME fields we learned work on records[]
        # =========================================================
        section("F) Retry productDetail with known-good Product fields")
        if scalar_keys:
            qpd = f'''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {{
              productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {{
                __typename
                {scalar_part}
              }}
            }}'''
            r = post(c, qpd, {"s": "57", "id": SKU, "ctx": CTX})
            print(f"  status: {r.status_code}")
            try:
                body = r.json()
                print(f"  body:\n{json.dumps(body, indent=2)[:3000]}")
            except Exception:
                print(f"  raw: {r.text[:2000]}")
            save("F_productDetail_retry.json", r.text)

    print(f"\nDone. Output in {OUTDIR}")


if __name__ == "__main__":
    main()
