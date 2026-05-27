"""
H-E-B GraphQL Probe — Phase 7b
------------------------------
Now that we know Waldron Rd = store #57 (Flour Bluff H-E-B plus!), we can
skip the 700-store brute force entirely.

Phase 7b runs against the REAL Waldron store, finds all productDetail
fields, discovers subfield shapes for complex types, and probes
productSearch + browseCategory.
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
STORE_NUMBER = 57            # Waldron Rd / Flour Bluff H-E-B plus!
STORE_NUMBER_STR = "57"
KNOWN_GOOD_SKU = "1510154"   # Central Market Organics Instant Coffee


def safe(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9_\-.]', '_', s)[:80]


def save(name, data):
    p = OUTDIR / safe(name)
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
        # 0) Sanity check: store #57 is Flour Bluff
        # =========================================================
        section(f"0) Sanity check: store(storeNumber: {STORE_NUMBER})")
        q = f'''query Q {{
          store(storeNumber: {STORE_NUMBER}) {{
            storeNumber
            name
            phoneNumber
            latitude
            longitude
            address {{ streetAddress locality region postalCode country }}
          }}
        }}'''
        r = post_query(c, q)
        print(f"  status: {r.status_code}")
        print(f"  body: {r.text}")
        save("0_store_57.json", r.text)

        # =========================================================
        # A) productDetail baseline
        # =========================================================
        section(f"A) productDetail at store #{STORE_NUMBER}, SKU {KNOWN_GOOD_SKU}")
        q = '''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {
          productDetail(storeId: $s, id: $id, shoppingContext: $ctx) { __typename }
        }'''
        r = post_query(c, q, {"s": STORE_NUMBER_STR, "id": KNOWN_GOOD_SKU, "ctx": CTX})
        print(f"  status: {r.status_code}")
        print(f"  body: {r.text}")
        save("A_baseline.json", r.text)

        # =========================================================
        # B) productDetail: probe every field name (verbose)
        # =========================================================
        section("B) productDetail: verbose field-name probing")
        candidates = [
            "id", "productId", "sku", "upc", "ean", "productNumber",
            "uid", "code", "primaryProductId",
            "name", "displayName", "title", "productName", "label",
            "brand", "brandName", "manufacturer", "vendor",
            "price", "pricing", "prices", "regularPrice", "salePrice",
            "currentPrice", "listPrice", "unitPrice", "perUnitPrice",
            "displayPrice", "amount",
            "image", "images", "imageUrl", "imageUrls", "primaryImage",
            "thumbnail", "media", "imageGallery",
            "description", "longDescription", "shortDescription", "details",
            "productDescription",
            "available", "availability", "inStock", "isAvailable",
            "inventory", "inventoryState", "stockStatus", "stock",
            "isInStock", "outOfStock", "inAssortment",
            "category", "categories", "department", "taxonomy",
            "taxonomyPath", "breadcrumbs", "navigationPath",
            "size", "weight", "uom", "unitOfMeasure", "packaging",
            "packageSize", "containerSize",
            "url", "slug", "productUrl", "path", "permalink",
            "ingredients", "nutrition", "nutritionFacts", "specifications",
            "ratings", "reviews", "averageRating", "reviewCount",
            "tags", "labels", "attributes",
            "private", "isPrivateLabel", "ownedBrand", "isHebBrand",
            "warnings", "dietary", "dietaryAttributes",
        ]
        valid_scalar = {}
        valid_complex = []
        unknown_field = []
        other = []
        for f in candidates:
            q = f'''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {{
              productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {{ {f} }}
            }}'''
            try:
                r = post_query(c, q, {"s": STORE_NUMBER_STR, "id": KNOWN_GOOD_SKU, "ctx": CTX})
                body = r.json() if r.text.startswith("{") else None
            except Exception as e:
                print(f"  {f:25s} REQ_ERR: {e}")
                continue
            if not body:
                continue
            if "errors" in body:
                em = body["errors"][0]["message"]
                if 'Cannot query field' in em:
                    unknown_field.append(f)
                elif "must have a selection of subfields" in em:
                    m = re.search(r"of type [\"']([\w!\[\]]+)[\"']", em)
                    inner = m.group(1) if m else "?"
                    valid_complex.append({"field": f, "type": inner})
                    print(f"  ⊞ {f:25s} NEEDS_SUBFIELDS type={inner}")
                else:
                    print(f"  ? {f:25s} OTHER: {em[:180]}")
                    other.append((f, em))
            else:
                data = body.get("data")
                pd = (data or {}).get("productDetail")
                if isinstance(pd, dict) and f in pd:
                    v = pd[f]
                    valid_scalar[f] = v
                    preview = json.dumps(v)[:140] if v is not None else "null"
                    print(f"  ✓ {f:25s} -> {preview}")
                else:
                    other.append((f, f"unexpected: {pd}"))
            time.sleep(0.15)

        print(f"\n  Summary: {len(valid_scalar)} scalar | "
              f"{len(valid_complex)} complex | "
              f"{len(unknown_field)} unknown")
        save("B_scalar.json", valid_scalar)
        save("B_complex.json", valid_complex)
        save("B_unknown.json", unknown_field)
        save("B_other.json", other)

        # B2: Full query with all valid scalars
        if valid_scalar:
            section("B2) Full productDetail with all scalars")
            scalar_keys = list(valid_scalar.keys())
            qfull = f'''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {{
              productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {{
                __typename
                {chr(10).join('    ' + k for k in scalar_keys)}
              }}
            }}'''
            r = post_query(c, qfull, {"s": STORE_NUMBER_STR, "id": KNOWN_GOOD_SKU, "ctx": CTX})
            print(f"  status: {r.status_code}")
            print(f"  body (first 3500):\n{r.text[:3500]}")
            save("B2_FULL_PRODUCT.json", r.text)

        # B3: Probe subfields of complex types
        if valid_complex:
            section("B3) Probe subfields of complex types")
            subfield_candidates = [
                "url", "uri", "src", "href", "path",
                "value", "amount", "currency", "currencyCode",
                "price", "regularPrice", "salePrice", "displayPrice", "rawPrice",
                "min", "max", "isOnSale", "isSale",
                "id", "name", "label", "displayName", "title",
                "available", "inStock", "state", "status", "level",
                "small", "medium", "large", "primary", "alt",
                "type", "kind", "format",
                "categoryId", "categoryName",
            ]
            complex_info = {}
            for cf in valid_complex:
                fname = cf["field"]
                ftype = cf["type"]
                print(f"\n  --- {fname} ({ftype}) ---")
                found = {}
                for sf in subfield_candidates:
                    q = f'''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {{
                      productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {{ {fname} {{ {sf} }} }}
                    }}'''
                    r = post_query(c, q, {"s": STORE_NUMBER_STR, "id": KNOWN_GOOD_SKU, "ctx": CTX})
                    try:
                        b = r.json()
                        if "errors" not in b:
                            pd = (b.get("data") or {}).get("productDetail") or {}
                            val = pd.get(fname)
                            preview = json.dumps(val)[:160] if val else "null/empty"
                            print(f"    ✓ {sf:20s} -> {preview}")
                            found[sf] = val
                        else:
                            em = b["errors"][0]["message"]
                            if "must have" in em and "Cannot query field" not in em:
                                m = re.search(r"of type [\"']([\w!\[\]]+)[\"']", em)
                                inner = m.group(1) if m else "?"
                                print(f"    ⊞ {sf:20s} NESTED type={inner}")
                                found[sf] = f"<{inner}>"
                    except Exception:
                        pass
                    time.sleep(0.12)
                complex_info[fname] = {"type": ftype, "subfields": found}
            save("B3_complex_subfields.json", complex_info)

        # =========================================================
        # D) productSearch with Int storeId
        # =========================================================
        section("D) productSearch: storeId as Int")
        ps_attempts = [
            ("query_arg", 'query Q($s: Int!, $ctx: ShoppingContext!, $q: String!) { productSearch(storeId: $s, shoppingContext: $ctx, query: $q) { __typename } }',
             {"s": STORE_NUMBER, "ctx": CTX, "q": "coffee"}),
            ("q_arg", 'query Q($s: Int!, $ctx: ShoppingContext!, $q: String!) { productSearch(storeId: $s, shoppingContext: $ctx, q: $q) { __typename } }',
             {"s": STORE_NUMBER, "ctx": CTX, "q": "coffee"}),
            ("searchString", 'query Q($s: Int!, $ctx: ShoppingContext!, $q: String!) { productSearch(storeId: $s, shoppingContext: $ctx, searchString: $q) { __typename } }',
             {"s": STORE_NUMBER, "ctx": CTX, "q": "coffee"}),
            ("phrase", 'query Q($s: Int!, $ctx: ShoppingContext!, $q: String!) { productSearch(storeId: $s, shoppingContext: $ctx, phrase: $q) { __typename } }',
             {"s": STORE_NUMBER, "ctx": CTX, "q": "coffee"}),
            ("text", 'query Q($s: Int!, $ctx: ShoppingContext!, $q: String!) { productSearch(storeId: $s, shoppingContext: $ctx, text: $q) { __typename } }',
             {"s": STORE_NUMBER, "ctx": CTX, "q": "coffee"}),
            ("noQ", 'query Q($s: Int!, $ctx: ShoppingContext!) { productSearch(storeId: $s, shoppingContext: $ctx) { __typename } }',
             {"s": STORE_NUMBER, "ctx": CTX}),
        ]
        for tag, q, vars_ in ps_attempts:
            r = post_query(c, q, vars_)
            em = err_msg(r)
            ok = r.status_code == 200 and "errors" not in r.text
            print(f"  tag={tag:15s} ok={ok} status={r.status_code} err={em[:240]}")
            if ok:
                print(f"    body: {r.text[:600]}")
            save(f"D_ps_{tag}.json", r.text)
            time.sleep(0.4)

        # =========================================================
        # E) browseCategory: storeId Int + category IDs
        # =========================================================
        section("E) browseCategory")
        cids = [
            "coffee", "grocery", "central-market", "central_market",
            "490086", "100", "1", "DRINKS", "BEVERAGES",
            "490086_0_0", "coffee-tea", "all-products",
            "brand", "house-brand", "private-label",
        ]
        for cid in cids:
            q = '''query Q($s: Int!, $ctx: ShoppingContext!, $cid: String!) {
              browseCategory(storeId: $s, shoppingContext: $ctx, categoryId: $cid) { __typename }
            }'''
            r = post_query(c, q, {"s": STORE_NUMBER, "ctx": CTX, "cid": cid})
            em = err_msg(r)
            ok = r.status_code == 200 and "errors" not in r.text
            print(f"  cid={cid:25s} ok={ok} status={r.status_code} err={em[:180]}")
            if ok:
                print(f"    body: {r.text[:600]}")
            save(f"E_bc_{safe(cid)}.json", r.text)
            time.sleep(0.3)

    print(f"\nDone. Output in {OUTDIR}")


if __name__ == "__main__":
    main()
