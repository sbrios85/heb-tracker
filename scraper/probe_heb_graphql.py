"""
H-E-B GraphQL Probe — Phase 6
-----------------------------
Phase 5 confirmed:
  - ShoppingContext value: CURBSIDE_PICKUP (returns data!)
  - PostalAddress fields: streetAddress, locality, region, postalCode, country
  - productSearch exists (requires shoppingContext: ShoppingContext!)
  - browseCategory exists (requires categoryId: String!)
  - Store 92 = Victoria H-E-B plus! (still need Waldron's number)

Phase 6 goals:
  A) Get the actual product data for SKU 1510154 with CURBSIDE_PICKUP context
     (Phase 5 final-fetch had a bug — rebuilding it properly).
  B) Discover full productDetail field set, including which fields need
     subfields. Use one-field-at-a-time probing and capture VALID + the
     "needs subfields" hint to learn nested types.
  C) Find Waldron Rd store. Strategy: brute-force adjacent store numbers
     starting from 92 (try 1-700, fast — just request name + postalCode).
     Corpus Christi stores will turn up.
  D) Probe productSearch full argument shape.
  E) Probe browseCategory full argument shape — and try common H-E-B
     category IDs to enumerate.
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
KNOWN_GOOD_SKU = "1510154"  # Central Market Organics Instant Coffee


def save(name, data):
    p = OUTDIR / name
    if isinstance(data, (dict, list)):
        p.write_text(json.dumps(data, indent=2))
    else:
        p.write_text(str(data))
    return p


def section(title):
    print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")


def post_query(client, query, variables=None):
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    return client.post(ENDPOINT, json=payload)


def err_msg(r):
    try:
        return r.json()["errors"][0]["message"]
    except Exception:
        return r.text[:200]


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        section("Setup")
        c.get(HOMEPAGE)
        print(f"  cookies: {list(c.cookies.keys())}")

        # =========================================================
        # A) Get a real productDetail response
        # =========================================================
        section(f"A) productDetail with ctx={CTX} and __typename")
        q = '''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {
          productDetail(storeId: $s, id: $id, shoppingContext: $ctx) { __typename }
        }'''
        r = post_query(c, q, {"s": "92", "id": KNOWN_GOOD_SKU, "ctx": CTX})
        print(f"  status: {r.status_code}")
        print(f"  body: {r.text}")
        save("A_productDetail_typename.json", r.text)

        # Try with productDetails (plural) as inline fragment selection — the
        # response type might be a union. The error from Phase 5 said
        # "ProductDetailResult" so it might be a union/interface.
        section("A2) productDetail probe with union selection")
        q2 = '''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {
          productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {
            __typename
            ... on Product {
              __typename
            }
          }
        }'''
        r = post_query(c, q2, {"s": "92", "id": KNOWN_GOOD_SKU, "ctx": CTX})
        print(f"  status: {r.status_code}")
        print(f"  body: {r.text[:600]}")
        save("A2_productDetail_union.json", r.text)
        # The error if "Product" doesn't exist will tell us actual concrete types.

        # =========================================================
        # B) Discover ALL productDetail field names (one at a time)
        # =========================================================
        section("B) productDetail: probe field names one-at-a-time, full capture")
        candidates = [
            # IDs
            "id", "productId", "sku", "upc", "ean", "productNumber",
            "uid", "code", "primaryProductId",
            # Names
            "name", "displayName", "title", "productName", "label",
            # Brand
            "brand", "brandName", "manufacturer", "vendor",
            # Pricing
            "price", "pricing", "prices", "regularPrice", "salePrice",
            "currentPrice", "listPrice", "unitPrice", "perUnitPrice",
            "displayPrice", "amount",
            # Images
            "image", "images", "imageUrl", "imageUrls", "primaryImage",
            "thumbnail", "media", "imageGallery",
            # Description
            "description", "longDescription", "shortDescription", "details",
            "productDescription",
            # Availability/inventory
            "available", "availability", "inStock", "isAvailable",
            "inventory", "inventoryState", "stockStatus", "stock",
            "isInStock", "outOfStock",
            # Categories
            "category", "categories", "department", "taxonomy",
            "taxonomyPath", "breadcrumbs", "navigationPath",
            # Size / packaging
            "size", "weight", "uom", "unitOfMeasure", "packaging",
            "packageSize", "containerSize",
            # URL / slug
            "url", "slug", "productUrl", "path", "permalink",
            # Other useful stuff
            "ingredients", "nutrition", "nutritionFacts", "specifications",
            "ratings", "reviews", "averageRating", "reviewCount",
            "tags", "labels", "attributes",
            "private", "isPrivateLabel", "ownedBrand", "isHebBrand",
        ]
        valid_scalar = {}     # field -> sample value
        valid_complex = []    # field -> needs subfields
        for f in candidates:
            q = f'''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {{
              productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {{ {f} }}
            }}'''
            r = post_query(c, q, {"s": "92", "id": KNOWN_GOOD_SKU, "ctx": CTX})
            try:
                body = r.json()
                if "errors" not in body:
                    pd = body.get("data", {}).get("productDetail")
                    if isinstance(pd, dict):
                        v = pd.get(f)
                        valid_scalar[f] = v
                        preview = json.dumps(v)[:140] if v is not None else "null"
                        print(f"  ✓ {f:25s} -> {preview}")
                else:
                    em = body["errors"][0]["message"]
                    if "must have a selection of subfields" in em or "of type" in em and "must have" in em:
                        valid_complex.append({"field": f, "msg": em[:300]})
                        # Extract the inner type from "Field 'foo' of type 'TypeName!' must have a selection of subfields"
                        m = re.search(r"of type [\"']([\w!\[\]]+)[\"']", em)
                        inner_type = m.group(1) if m else "?"
                        print(f"  ⊞ {f:25s} NEEDS SUBFIELDS, type={inner_type}")
            except Exception:
                pass
            time.sleep(0.18)
        save("B_valid_scalar_fields.json", valid_scalar)
        save("B_complex_fields.json", valid_complex)

        # One big query with all valid scalar fields
        if valid_scalar:
            section("B2) Full productDetail query with all valid scalar fields")
            scalar_keys = list(valid_scalar.keys())
            qfull = f'''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {{
              productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {{
                __typename
                {chr(10).join('    ' + k for k in scalar_keys)}
              }}
            }}'''
            r = post_query(c, qfull, {"s": "92", "id": KNOWN_GOOD_SKU, "ctx": CTX})
            print(f"  status: {r.status_code}")
            print(f"  body (first 2500):\n{r.text[:2500]}")
            save("B2_PRODUCT_DETAIL_FULL.json", r.text)

        # =========================================================
        # C) Brute-force store numbers to find Waldron (78418)
        # =========================================================
        section("C) Brute-force store numbers to find Corpus Christi stores")
        # H-E-B store numbers go up to ~700+. We'll query in chunks.
        # Strategy: check stores in batches; for each store get name + postalCode
        # to identify Corpus stores (78xxx).
        corpus_stores = []
        # Check 1..500 (covers most active stores). We do these in serial
        # but only 1 request each, and bail early if we find Waldron.
        # Use a compound query to make this faster — 10 stores per request as aliases.
        BATCH_SIZE = 20
        TOTAL_RANGE = 700
        for start in range(1, TOTAL_RANGE + 1, BATCH_SIZE):
            batch = list(range(start, min(start + BATCH_SIZE, TOTAL_RANGE + 1)))
            # Build aliased query: s001: store(storeNumber: 1) { ... }
            aliases = []
            for n in batch:
                aliases.append(f'''s{n}: store(storeNumber: {n}) {{
                    storeNumber name
                    address {{ streetAddress locality region postalCode }}
                }}''')
            q = "query Q {\n" + "\n".join(aliases) + "\n}"
            r = post_query(c, q)
            try:
                body = r.json()
                data = body.get("data") or {}
                for n in batch:
                    s = data.get(f"s{n}")
                    if s and isinstance(s, dict):
                        pc = (s.get("address") or {}).get("postalCode", "")
                        loc = (s.get("address") or {}).get("locality", "")
                        if pc.startswith("78") or "CORPUS" in (loc or "").upper():
                            entry = {
                                "storeNumber": s.get("storeNumber"),
                                "name": s.get("name"),
                                "address": s.get("address"),
                            }
                            corpus_stores.append(entry)
                            print(f"  ✓ #{n}: {s.get('name')} — {(s.get('address') or {}).get('streetAddress')}, {loc} {pc}")
            except Exception as e:
                print(f"  batch {start}-{start+BATCH_SIZE} parse error: {e}")
            # Brief pause between batches to be polite
            time.sleep(0.4)
        save("C_corpus_stores.json", corpus_stores)
        print(f"\n  Found {len(corpus_stores)} stores with zip 78xxx or locality CORPUS")

        # Identify Waldron specifically
        waldron = None
        for s in corpus_stores:
            sa = (s.get("address") or {}).get("streetAddress", "")
            if "WALDRON" in sa.upper():
                waldron = s
                print(f"  ⭐ WALDRON FOUND: #{s['storeNumber']} {s['name']} — {sa}")
                save("WALDRON_STORE.json", s)
                break

        # =========================================================
        # D) Probe productSearch full arg shape
        # =========================================================
        section("D) productSearch: probe argument shape")
        # We know it needs shoppingContext. Try common search argument names.
        ps_attempts = [
            ('query Q($s: ID!, $ctx: ShoppingContext!, $q: String!) { productSearch(storeId: $s, shoppingContext: $ctx, query: $q) { __typename } }',
             {"s": "92", "ctx": CTX, "q": "coffee"}),
            ('query Q($s: ID!, $ctx: ShoppingContext!, $q: String!) { productSearch(storeId: $s, shoppingContext: $ctx, searchTerm: $q) { __typename } }',
             {"s": "92", "ctx": CTX, "q": "coffee"}),
            ('query Q($s: ID!, $ctx: ShoppingContext!, $q: String!) { productSearch(storeId: $s, shoppingContext: $ctx, term: $q) { __typename } }',
             {"s": "92", "ctx": CTX, "q": "coffee"}),
            ('query Q($s: ID!, $ctx: ShoppingContext!, $q: String!) { productSearch(storeId: $s, shoppingContext: $ctx, keyword: $q) { __typename } }',
             {"s": "92", "ctx": CTX, "q": "coffee"}),
            ('query Q($s: ID!, $ctx: ShoppingContext!) { productSearch(storeId: $s, shoppingContext: $ctx) { __typename } }',
             {"s": "92", "ctx": CTX}),
        ]
        for q, vars_ in ps_attempts:
            r = post_query(c, q, vars_)
            shape = re.search(r"productSearch\([^)]*\)", q)
            shape_str = shape.group(0) if shape else "?"
            em = err_msg(r)
            ok = r.status_code == 200 and "errors" not in r.text
            print(f"  {shape_str[:80]:80s} ok={ok} err={em[:140]}")
            if ok:
                print(f"    body: {r.text[:400]}")
            save(f"D_ps_{shape_str[:40].replace(' ','_')}.json", r.text)
            time.sleep(0.4)

        # =========================================================
        # E) Probe browseCategory arg shape, find category IDs
        # =========================================================
        section("E) browseCategory: probe arg shape")
        # We KNOW categoryId: String! is required. Try plausible category IDs.
        # Some Apollo apps use slugs ("coffee"), some use UUIDs, some integers.
        # We'll probe with several to see which work.
        bc_attempts = [
            ('query Q($s: ID!, $ctx: ShoppingContext!, $cid: String!) { browseCategory(storeId: $s, shoppingContext: $ctx, categoryId: $cid) { __typename } }',
             {"s": "92", "ctx": CTX, "cid": "coffee"}),
            ('query Q($s: ID!, $ctx: ShoppingContext!, $cid: String!) { browseCategory(storeId: $s, shoppingContext: $ctx, categoryId: $cid) { __typename } }',
             {"s": "92", "ctx": CTX, "cid": "490086"}),  # try a numeric SKU as cat
            ('query Q($s: ID!, $ctx: ShoppingContext!, $cid: String!) { browseCategory(storeId: $s, shoppingContext: $ctx, categoryId: $cid) { __typename } }',
             {"s": "92", "ctx": CTX, "cid": "grocery"}),
            ('query Q($s: ID!, $ctx: ShoppingContext!, $cid: String!) { browseCategory(storeId: $s, shoppingContext: $ctx, categoryId: $cid) { __typename } }',
             {"s": "92", "ctx": CTX, "cid": "central-market"}),
            # bare with required args only:
            ('query Q($cid: String!) { browseCategory(categoryId: $cid) { __typename } }',
             {"cid": "coffee"}),
        ]
        for q, vars_ in bc_attempts:
            r = post_query(c, q, vars_)
            em = err_msg(r)
            ok = r.status_code == 200 and "errors" not in r.text
            print(f"  cid={vars_.get('cid')!r:25s} ok={ok} status={r.status_code} err={em[:140]}")
            if ok:
                print(f"    body: {r.text[:400]}")
            save(f"E_bc_{vars_.get('cid','none')}.json", r.text)
            time.sleep(0.4)

    print(f"\nDone. Output in {OUTDIR}")


if __name__ == "__main__":
    main()
