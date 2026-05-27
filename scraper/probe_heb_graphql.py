"""
H-E-B GraphQL Probe — Phase 8
-----------------------------
Phase 7b confirmed:
  - Store #57 = Flour Bluff H-E-B plus! (Waldron Rd) ✓
  - productSearch(storeId: Int, shoppingContext, query: String) -> ProductCollection
  - browseCategory(categoryId: "490086") -> BrowseProductCollection
  - But: productDetail at store 57 with our SKU returned ZERO valid fields
    on type "Product". Something is off about Product specifically.

Phase 8 goals:
  A) Investigate Product type directly — maybe productDetail returns a
     union/interface and Product is just an interface stub. Try inline
     fragments to see what concrete types exist.
  B) Probe ProductCollection (from productSearch) — find fields like
     items, products, results, totalCount, pageInfo.
  C) Probe BrowseProductCollection similarly.
  D) Once we find items/products on the collection, probe THAT item type
     for the actual fields (name, brand, price, image).
  E) productSearch with more args (limit, page, filters, brand).
  F) browseCategory with subfield probe — to find what categoryIds we
     can enumerate.
  G) Try Product fields with __SCHEMA__-style fragment introspection
     (some Apollo versions have partial introspection on types).
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
STORE_STR = "57"
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


def probe_field_on(c, parent_query, parent_path, candidate_fields, label):
    """Generic helper: probe candidate field names on a particular path in a query.
    parent_query is a query template with __FIELD__ placeholder where the candidate
    name should be inserted. Returns dict of valid_scalar, complex, unknown.
    """
    valid_scalar = {}
    valid_complex = []
    unknown = []
    other = []
    for f in candidate_fields:
        q = parent_query.replace("__FIELD__", f)
        try:
            r = post(c, q)
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
                # Print these — they often say "X must be a Y" or "argument required"
                print(f"  ? {f:25s} {em[:200]}")
        else:
            # Walk to extract value at the path
            cur = body.get("data") or {}
            for key in parent_path:
                if isinstance(cur, dict):
                    cur = cur.get(key)
                else:
                    cur = None
                    break
            if isinstance(cur, dict) and f in cur:
                v = cur[f]
                valid_scalar[f] = v
                print(f"  ✓ {f:25s} -> {json.dumps(v)[:140] if v is not None else 'null'}")
            else:
                other.append((f, f"no_field_in_path: cur={cur}"))
        time.sleep(0.12)
    print(f"\n  [{label}] scalar={len(valid_scalar)} complex={len(valid_complex)} unknown={len(unknown)} other={len(other)}")
    return {"scalar": valid_scalar, "complex": valid_complex, "unknown": unknown, "other": other}


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        section("Setup")
        c.get(HOMEPAGE)

        # =========================================================
        # A) productDetail: is it actually a union/interface?
        # =========================================================
        section("A) productDetail: probe for union/interface members")
        # If `Product` is an interface, we can use inline fragments naming
        # candidate concrete subtypes. Try plausible names — we'll see which
        # the server accepts.
        member_candidates = [
            "Product", "ProductDetails", "ProductDetail",
            "GroceryProduct", "RetailProduct", "ShoppableProduct",
            "PrivateLabelProduct", "GenericProduct",
            "ProductData", "ProductV2", "RawProduct",
        ]
        for t in member_candidates:
            q = f'''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {{
              productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {{
                __typename
                ... on {t} {{ __typename }}
              }}
            }}'''
            r = post(c, q, {"s": STORE_STR, "id": SKU, "ctx": CTX})
            try:
                body = r.json()
                if "errors" in body:
                    em = body["errors"][0]["message"]
                    if f'type "{t}"' in em and "is not a member" in em:
                        # Apollo error: "Fragment cannot be spread here as objects of
                        # type 'Product' can never be of type 'X'."
                        # — meaning X exists but Product isn't related.
                        pass
                    elif "Unknown type" in em or f'"{t}"' in em:
                        # Unknown type: definitely doesn't exist
                        pass
                    else:
                        print(f"  {t:25s} ERR: {em[:200]}")
                else:
                    print(f"  {t:25s} OK: {r.text[:200]}")
            except Exception:
                pass
            time.sleep(0.2)

        # =========================================================
        # A2) Maybe productDetail.Product has snake_case fields
        # =========================================================
        section("A2) productDetail.Product: try snake_case fields")
        snake_candidates = [
            "id", "product_id", "display_name", "product_name",
            "brand", "brand_name", "primary_image", "image_url",
            "price", "regular_price", "current_price",
            "in_stock", "is_available",
            "category", "categories",
            "size", "package_size", "unit_of_measure",
            "url", "product_url",
            "description", "long_description",
        ]
        template = f'''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {{
          productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {{ __FIELD__ }}
        }}'''
        # We need vars — but probe_field_on doesn't accept vars. Inline values:
        template_inline = template.replace("$s", f'"{STORE_STR}"').replace("$id", f'"{SKU}"').replace("$ctx", CTX)
        template_inline = template_inline.replace(", $ctx: ShoppingContext!", "")
        template_inline = template_inline.replace("$s: ID!, $id: ID!,", "")
        # Actually easier: just hardcode the query without variables.
        inline_q = '''query {
          productDetail(storeId: "57", id: "1510154", shoppingContext: CURBSIDE_PICKUP) { __FIELD__ }
        }'''
        snake_result = probe_field_on(c, inline_q, ["productDetail"], snake_candidates, "snake_case")
        save("A2_snake.json", snake_result)

        # =========================================================
        # A3) Maybe Product fields require fragment on a parent type
        # =========================================================
        section("A3) productDetail.Product: try Apollo-style fragment fields")
        # From the JS we saw mentions of STORE_DETAILS_FRAGMENT etc. The Product
        # type might require spread fragments. Try fields with PRODUCT_ prefix.
        # Also try fields from the recipeDetail snippet we saw earlier — recipes
        # had similar context, and maybe Product has fields named like
        # "primaryProductId" "productGroupId" etc.
        more_candidates = [
            "primaryProductId", "productGroupId", "productGroup",
            "displayedName", "displayedBrand", "displayedSize",
            "branding", "imagery", "imageObject",
            "salePriceInfo", "priceInfo", "priceDetails",
            "stockInfo", "stockState", "isOutOfStock",
            "primaryImage", "primaryImageUrl",
            "productName", "productBrand", "productPrice",
            "longDescriptionHtml", "shortDescriptionHtml",
            "promotionalText", "isDiscontinued", "isActive",
            "departmentName", "categoryName", "subcategoryName",
            "departmentId", "categoryId",
            "fulfillmentChannels", "fulfillment",
            "unitPriceText", "displayedUnitPrice",
        ]
        more_result = probe_field_on(c, inline_q, ["productDetail"], more_candidates, "more_candidates")
        save("A3_more.json", more_result)

        # =========================================================
        # B) ProductCollection (from productSearch) field discovery
        # =========================================================
        section("B) ProductCollection: probe fields")
        ps_template = f'''query Q {{
          productSearch(storeId: {STORE}, shoppingContext: {CTX}, query: "coffee") {{ __FIELD__ }}
        }}'''
        pc_candidates = [
            "__typename", "items", "products", "results", "records",
            "data", "nodes", "edges",
            "totalCount", "total", "count", "totalResults",
            "page", "pageInfo", "hasNextPage", "hasMore",
            "facets", "filters", "aggregations",
            "query", "term", "phrase",
            "categories", "brands",
            "navigation", "breadcrumbs",
        ]
        pc_result = probe_field_on(c, ps_template, ["productSearch"], pc_candidates, "ProductCollection")
        save("B_ProductCollection.json", pc_result)

        # =========================================================
        # C) BrowseProductCollection field discovery
        # =========================================================
        section("C) BrowseProductCollection: probe fields")
        bc_template = f'''query Q {{
          browseCategory(storeId: {STORE}, shoppingContext: {CTX}, categoryId: "490086") {{ __FIELD__ }}
        }}'''
        bpc_result = probe_field_on(c, bc_template, ["browseCategory"], pc_candidates, "BrowseProductCollection")
        save("C_BrowseProductCollection.json", bpc_result)

        # =========================================================
        # D) If "items" or "products" exists, probe THAT type's fields
        # =========================================================
        section("D) Probe items/products subtype")
        # If productSearch.items returns a list, we need to know the element type.
        # We probe items{__typename} first.
        items_field_name = None
        for f in ["items", "products", "results", "records", "nodes"]:
            q = f'''query Q {{
              productSearch(storeId: {STORE}, shoppingContext: {CTX}, query: "coffee") {{
                {f} {{ __typename }}
              }}
            }}'''
            r = post(c, q)
            try:
                body = r.json()
                if "errors" not in body:
                    items_field_name = f
                    val = (body.get("data") or {}).get("productSearch", {}).get(f)
                    print(f"  ProductCollection.{f} works: {json.dumps(val)[:600]}")
                    save(f"D_items_via_{f}.json", body)
                    break
            except Exception:
                pass
            time.sleep(0.2)

        if items_field_name:
            # Get the typename of one item
            q = f'''query Q {{
              productSearch(storeId: {STORE}, shoppingContext: {CTX}, query: "coffee") {{
                {items_field_name} {{ __typename }}
              }}
            }}'''
            r = post(c, q)
            print(f"\n  items[__typename]: {r.text[:600]}")

            # Now probe candidate fields on each item
            item_candidates = [
                "id", "productId", "sku", "upc",
                "name", "displayName", "title", "productName",
                "brand", "brandName",
                "price", "displayPrice", "regularPrice",
                "image", "imageUrl", "primaryImage", "thumbnail",
                "url", "slug", "productUrl",
                "available", "inStock", "isAvailable",
                "size", "packageSize",
                "category", "categoryName",
                "description", "shortDescription",
                "inventoryState",
            ]
            item_template = f'''query Q {{
              productSearch(storeId: {STORE}, shoppingContext: {CTX}, query: "coffee") {{
                {items_field_name} {{ __FIELD__ }}
              }}
            }}'''
            item_result = probe_field_on(c, item_template, ["productSearch", items_field_name], item_candidates, "Item")
            save("D_item_fields.json", item_result)

            # Big query
            if item_result["scalar"]:
                section("D2) Big productSearch + items query")
                keys = list(item_result["scalar"].keys())
                qbig = f'''query Q {{
                  productSearch(storeId: {STORE}, shoppingContext: {CTX}, query: "coffee") {{
                    {items_field_name} {{
                      __typename
                      {chr(10).join("    " + k for k in keys)}
                    }}
                  }}
                }}'''
                r = post(c, qbig)
                print(f"  status: {r.status_code}")
                print(f"  body (first 4000):\n{r.text[:4000]}")
                save("D2_PRODUCT_SEARCH_FULL.json", r.text)

        # =========================================================
        # E) productSearch with additional args
        # =========================================================
        section("E) productSearch: additional arg discovery")
        extra_arg_attempts = [
            ("limit", 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", limit: 3) { __typename } }'),
            ("first", 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", first: 3) { __typename } }'),
            ("pageSize", 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", pageSize: 3) { __typename } }'),
            ("page", 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", page: 1) { __typename } }'),
            ("offset", 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", offset: 0) { __typename } }'),
            ("filter", 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", filter: "brand") { __typename } }'),
            ("brand", 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", brand: "Central Market") { __typename } }'),
            ("sort", 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", sort: "PRICE_ASC") { __typename } }'),
            ("sortBy", 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", sortBy: "PRICE_ASC") { __typename } }'),
            ("filters", 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", filters: []) { __typename } }'),
        ]
        for tag, q in extra_arg_attempts:
            r = post(c, q)
            try:
                em = r.json()["errors"][0]["message"] if "errors" in r.json() else "<ok>"
            except Exception:
                em = r.text[:200]
            ok = r.status_code == 200 and "errors" not in r.text
            print(f"  {tag:12s} ok={ok} status={r.status_code} err={em[:200]}")
            save(f"E_{tag}.json", r.text)
            time.sleep(0.3)

        # =========================================================
        # F) browseCategory: enumerate categories
        # =========================================================
        section("F) browseCategory subfield + category enumeration")
        # Same fields as ProductCollection probably; print sample data
        q = f'''query Q {{
          browseCategory(storeId: {STORE}, shoppingContext: {CTX}, categoryId: "490086") {{
            __typename
            {chr(10).join("    " + k for k in pc_result["scalar"]) if pc_result.get("scalar") else "    __typename"}
          }}
        }}'''
        r = post(c, q)
        print(f"  body (first 1500): {r.text[:1500]}")
        save("F_browseCategory_full.json", r.text)

    print(f"\nDone. Output in {OUTDIR}")


if __name__ == "__main__":
    main()
