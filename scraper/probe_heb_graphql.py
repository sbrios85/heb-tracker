"""
H-E-B GraphQL Probe — Phase 5
-----------------------------
Phase 4 discoveries:
  - ShoppingContext is an ENUM (not an input object). Pass a string value.
  - store(storeNumber: Int) works. Store 92 = Victoria H-E-B plus! (wrong store)
  - Store fields: storeNumber, name, phoneNumber, latitude, longitude, address (PostalAddress!)
  - storeById(storeNumber: $storeId) is the canonical operation name
  - 38 real operations harvested from JS, including:
      NearbyStores, StoreSearch, SearchStoresByNearbyStore, StorePickerSearch
      StoreDetailsPage, StoreAddress, SearchStoresByProductId

Phase 5 goals:
  A) Brute-force the ShoppingContext enum values.
  B) Probe NearbyStores to find Waldron Rd store from zip 78418.
  C) Probe PostalAddress subfields (we need address1, city, state, zip).
  D) Once we have ShoppingContext + real storeNumber, hit productDetail
     for a known SKU (1510154) and get a FULL real product response.
  E) Probe for category-browsing operations (productSearch, browseCategory,
     productsByBrand, searchProducts, etc.) — the discovery scraper needs
     to ENUMERATE products, not just look up by ID.
  F) Extract more JS chunks looking for productSearch/browseCategory queries.
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
    if variables is not None:
        payload["variables"] = variables
    if op_name:
        payload["operationName"] = op_name
    return client.post(ENDPOINT, json=payload)


def err_msg(r):
    try:
        return r.json()["errors"][0]["message"]
    except Exception:
        return r.text[:200]


def all_err_msgs(r):
    try:
        return [e.get("message", "") for e in r.json().get("errors", [])]
    except Exception:
        return [r.text[:300]]


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        # ---- Setup ----
        section("Setup")
        r = c.get(HOMEPAGE)
        homepage_html = r.text
        print(f"  homepage status: {r.status_code}")

        # =========================================================
        # A) ShoppingContext enum value discovery
        # =========================================================
        section("A) ShoppingContext enum: brute-force values")
        # Pass a definitely-invalid value to leak the allowed list. Some Apollo
        # servers respond "Value 'X' does not exist in 'ShoppingContext' enum. Did you mean Y?"
        q = '''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {
          productDetail(storeId: $s, id: $id, shoppingContext: $ctx) { __typename }
        }'''

        # Step 1: try a garbage value to see if the server lists valid values.
        r = post_query(c, q, {"s": "92", "id": "1510154", "ctx": "__INVALID_VALUE__"})
        msgs = all_err_msgs(r)
        print("  garbage-value response errors:")
        for m in msgs[:5]:
            print(f"    {m[:300]}")
        save("ctx_garbage.json", r.text)

        # Step 2: try every plausible enum value.
        # Common Apollo conventions: SCREAMING_SNAKE_CASE.
        ctx_candidates = [
            "PICKUP", "CURBSIDE", "DELIVERY", "SHIPPING", "IN_STORE", "INSTORE",
            "INSTORE_SHOPPING", "STORE_PICKUP", "CURBSIDE_PICKUP",
            "HOME_DELIVERY", "STANDARD_DELIVERY", "MAIL", "SHIP",
            "DEFAULT", "NONE", "UNKNOWN",
        ]
        print("\n  trying enum values:")
        working_ctx = None
        for v in ctx_candidates:
            r = post_query(c, q, {"s": "92", "id": "1510154", "ctx": v})
            em = err_msg(r)
            # If "does not exist in ShoppingContext" we know it's invalid.
            # If "Cannot query field __typename" — actually wait, __typename always works.
            # If we get data — jackpot.
            if r.status_code == 200:
                body = r.json()
                if body.get("data") is not None:
                    print(f"  ctx={v:25s} ✓ DATA RETURNED")
                    working_ctx = v
                    save(f"ctx_working_{v}.json", body)
                    break
                else:
                    print(f"  ctx={v:25s} status=200 but no data: {em[:120]}")
            else:
                # Show the specific enum error
                print(f"  ctx={v:25s} {em[:160]}")
            time.sleep(0.3)

        if not working_ctx:
            # Fallback: use whichever the server didn't reject as an enum violation
            print("\n  No ctx value returned data; checking which were accepted as valid enum:")
            for v in ctx_candidates:
                r = post_query(c, q, {"s": "92", "id": "1510154", "ctx": v})
                em = err_msg(r)
                if "does not exist in" not in em and "Enum" not in em:
                    print(f"    {v}: not an enum rejection -> {em[:180]}")
                time.sleep(0.2)

        # =========================================================
        # B) Find Waldron Rd store via NearbyStores
        # =========================================================
        section("B) Find Waldron Rd store (zip 78418) via NearbyStores")
        # We saw "NearbyStores" as an operation name. Try plausible shapes.
        # The op probably takes a zip or lat/lng.
        nearby_attempts = [
            ('query Q($zip: String!) { nearbyStores(zip: $zip) { __typename } }', {"zip": "78418"}),
            ('query Q($zip: String!) { nearbyStores(postalCode: $zip) { __typename } }', {"zip": "78418"}),
            ('query Q($zip: String!) { nearbyStores(zipCode: $zip) { __typename } }', {"zip": "78418"}),
            ('query Q($a: String!) { nearbyStores(address: $a) { __typename } }', {"a": "78418"}),
            ('query Q($lat: Float!, $lng: Float!) { nearbyStores(latitude: $lat, longitude: $lng) { __typename } }',
             {"lat": 27.6234, "lng": -97.2606}),
            ('query Q { nearbyStores { __typename } }', None),
        ]
        for q, vars_ in nearby_attempts:
            r = post_query(c, q, vars_)
            try:
                body = r.json()
                ok = "errors" not in body
                em = "" if ok else body["errors"][0]["message"]
                shape = re.search(r"nearbyStores\([^)]*\)", q)
                shape_str = shape.group(0) if shape else "no-args"
                print(f"  {shape_str:55s} status={r.status_code} ok={ok} err={em[:140]}")
                if ok:
                    save(f"nearbyStores_{shape_str.replace(' ', '_')[:40]}.json", body)
            except Exception as e:
                print(f"  parse error: {e}")
            time.sleep(0.3)

        # Now try StoreSearch (also in the operations list)
        print("\n  trying StoreSearch:")
        search_attempts = [
            ('query Q($q: String!) { storeSearch(query: $q) { __typename } }', {"q": "78418"}),
            ('query Q($q: String!) { storeSearch(address: $q) { __typename } }', {"q": "78418"}),
            ('query Q($q: String!) { storeSearch(zip: $q) { __typename } }', {"q": "78418"}),
            ('query Q($q: String!) { storeSearch(searchTerm: $q) { __typename } }', {"q": "78418"}),
            ('{ storeSearch { __typename } }', None),
        ]
        for q, vars_ in search_attempts:
            r = post_query(c, q, vars_)
            em = err_msg(r)
            shape = re.search(r"storeSearch\([^)]*\)|storeSearch", q)
            print(f"  {(shape.group(0) if shape else '?'):40s} status={r.status_code} err={em[:160]}")
            time.sleep(0.3)

        # Also try storeById (which we saw in JS)
        print("\n  trying storeById:")
        r = post_query(c, 'query Q($n: Int!) { storeById(storeNumber: $n) { __typename storeNumber name } }', {"n": 92})
        print(f"  storeById(92) status={r.status_code} body={r.text[:300]}")

        # =========================================================
        # C) PostalAddress subfield discovery
        # =========================================================
        section("C) PostalAddress: probe subfields on store(92).address")
        addr_field_candidates = [
            "line1", "line2", "addressLine1", "addressLine2",
            "street", "streetAddress", "address1", "address2",
            "city", "locality", "state", "stateCode", "region",
            "zip", "zipCode", "postalCode", "postal",
            "country", "countryCode",
        ]
        found_addr_fields = {}
        for f in addr_field_candidates:
            q = f'query Q($n: Int!) {{ store(storeNumber: $n) {{ address {{ {f} }} }} }}'
            r = post_query(c, q, {"n": 92})
            try:
                body = r.json()
                if body.get("data") and body["data"].get("store"):
                    val = body["data"]["store"]["address"].get(f)
                    found_addr_fields[f] = val
                    print(f"  addr.{f:18s} VALID -> {val}")
            except Exception:
                pass
            time.sleep(0.2)
        save("postal_address_fields.json", found_addr_fields)

        # =========================================================
        # D) Once context known, get a full productDetail response
        # =========================================================
        if working_ctx:
            section(f"D) FULL productDetail with ctx={working_ctx}")
            # Try a broad selection set first, find what's available
            big_field_set = [
                "id", "productId", "displayName", "name", "title",
                "description", "shortDescription", "longDescription",
                "brand", "brandName", "vendor",
                "image", "images", "imageUrl", "primaryImage",
                "price", "pricing", "regularPrice", "salePrice",
                "url", "slug", "productUrl",
                "category", "categories", "taxonomyPath",
                "size", "weight", "unitOfMeasure", "packaging",
                "available", "availability", "inventoryState", "inventory",
                "upc", "sku", "productNumber",
            ]
            # Try them one at a time so each one's validity is clear
            valid_fields = {}
            for f in big_field_set:
                qd = f'''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {{
                  productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {{ {f} }}
                }}'''
                r = post_query(c, qd, {"s": "92", "id": "1510154", "ctx": working_ctx})
                try:
                    body = r.json()
                    if "errors" not in body:
                        val = body.get("data", {}).get("productDetail", {})
                        if isinstance(val, dict):
                            v = val.get(f)
                            valid_fields[f] = v
                            preview = str(v)[:100] if v is not None else "null"
                            print(f"  field={f:25s} VALID -> {preview}")
                    else:
                        em = body["errors"][0]["message"]
                        if "Cannot query field" not in em and "of type" in em:
                            # Field exists but needs subfields
                            print(f"  field={f:25s} NEEDS_SUBFIELDS -> {em[:160]}")
                            valid_fields[f] = "<needs subfields>"
                except Exception:
                    pass
                time.sleep(0.25)
            save("productDetail_valid_fields.json", valid_fields)

            # Now do one big query with all valid scalar fields
            scalar_fields = [f for f, v in valid_fields.items() if v != "<needs subfields>"]
            print(f"\n  Final fetch with {len(scalar_fields)} scalar fields...")
            qfinal = f'''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {{
              productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {{
                {' '.join(scalar_fields)}
              }}
            }}'''
            r = post_query(c, qfinal, {"s": "92", "id": "1510154", "ctx": working_ctx})
            print(f"  final status: {r.status_code}")
            print(f"  final body (first 1500): {r.text[:1500]}")
            save("PRODUCT_DETAIL_FULL.json", r.text)
        else:
            print("\nD) SKIPPED - no working ShoppingContext value found yet")

        # =========================================================
        # E) Probe browse/search operations
        # =========================================================
        section("E) Probe product browse/search operations")
        # Operations we KNOW exist from harvested list — none was clearly a product
        # browse op. So we probe common Apollo conventions.
        browse_candidates = [
            "productSearch", "searchProducts", "products", "productList",
            "productListing", "browseProducts", "categoryProducts",
            "productsByCategory", "productsByBrand", "productsByDepartment",
            "browseCategory", "browseDepartment", "category", "categoryDetail",
            "department", "departmentDetail", "search", "shop",
            "shopByCategory", "shopByBrand",
        ]
        for op in browse_candidates:
            q = f'{{ {op} {{ __typename }} }}'
            r = post_query(c, q)
            em = err_msg(r)
            # Filter out "Cannot query field X" — that means non-existent
            if f'Cannot query field "{op}"' in em:
                continue
            print(f"  {op:25s} status={r.status_code} err={em[:200]}")
            time.sleep(0.2)

        # =========================================================
        # F) Grep more JS chunks for productSearch / browse / category queries
        # =========================================================
        section("F) Grep ALL JS chunks for product-listing queries")
        js_urls = re.findall(r'<script[^>]+src="([^"]+\.js[^"]*)"', homepage_html)
        cx_urls = [u for u in js_urls if "cx.static.heb.com" in u]
        print(f"  fetching all {len(cx_urls)} cx chunks...")

        all_operations = set()
        productSearch_snippets = []
        browse_snippets = []
        category_snippets = []
        brand_snippets = []
        gql_query_strings = []

        for i, url in enumerate(cx_urls):
            try:
                r = c.get(url, timeout=20.0)
                if r.status_code != 200:
                    continue
                js = r.text
                short = url.split("/")[-1]

                # Operation names
                ops = set()
                for pat in [
                    r'(?:query|mutation)\s+([A-Z]\w+)',
                    r'operationName\s*[:=]\s*["\']([A-Za-z]\w+)["\']',
                ]:
                    ops.update(re.findall(pat, js))
                all_operations.update(ops)

                # Capture full Apollo gql template literal strings.
                # Pattern: ["query Foo(...) { ... }"] or similar Apollo build output
                for m in re.finditer(r'\[["\`]((?:query|mutation)\s+\w+[^"`]{50,3500})["\`]', js):
                    gql_query_strings.append({
                        "chunk": short,
                        "body": m.group(1)[:3000],
                    })

                # Snippets around key terms
                for needle, bucket in [
                    ("productSearch", productSearch_snippets),
                    ("browseCategory", browse_snippets),
                    ("category(", category_snippets),
                    ("brand(", brand_snippets),
                ]:
                    for m in re.finditer(re.escape(needle) + r"[^`]{20,500}", js):
                        bucket.append({"chunk": short, "snippet": m.group(0)[:500]})
            except Exception as e:
                print(f"    {url} -> ERROR {e}")

        print(f"\n  total operations discovered: {len(all_operations)}")
        for op in sorted(all_operations):
            print(f"    {op}")
        save("operations_found_all.json", sorted(all_operations))

        print(f"\n  snippet bucket sizes:")
        print(f"    productSearch: {len(productSearch_snippets)}")
        print(f"    browseCategory: {len(browse_snippets)}")
        print(f"    category(: {len(category_snippets)}")
        print(f"    brand(: {len(brand_snippets)}")
        print(f"    full gql query strings: {len(gql_query_strings)}")
        save("snippets_productSearch.json", productSearch_snippets[:30])
        save("snippets_browseCategory.json", browse_snippets[:30])
        save("snippets_category.json", category_snippets[:30])
        save("snippets_brand.json", brand_snippets[:30])
        save("gql_query_strings.json", gql_query_strings[:200])

        if gql_query_strings:
            print(f"\n  --- sample full gql strings (first 8) ---")
            for s in gql_query_strings[:8]:
                print(f"  [{s['chunk']}]")
                print(f"    {s['body'][:600]}")
                print()

    print(f"\nDone. Output in {OUTDIR}")


if __name__ == "__main__":
    main()
