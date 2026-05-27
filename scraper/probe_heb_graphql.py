"""
H-E-B GraphQL Probe — Phase 4
-----------------------------
Phase 3 discoveries:
  - storeId=92 confirmed in __NEXT_DATA__ (homepage default, not Waldron yet)
  - productDetail requires: id: ID!, storeId: ID!, shoppingContext: ShoppingContext!
  - store(storeNumber: Int) — argument confirmed, just need an Int
  - JS chunks downloaded but wrong ones; only got minor operations

Phase 4 goals:
  A) Discover ShoppingContext input-type shape (probe field names on the input).
  B) Verify store(storeNumber: 92) works and see Store fields.
  C) Find a stores-by-zip / nearby-stores operation to locate Waldron (zip 78418).
  D) Download many more JS chunks (especially the big ones) and grep harder
     for queries containing "productDetail", "ShoppingContext", "productSearch",
     "category", "browse", "store".
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
STORE_ID = "92"  # from __NEXT_DATA__


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


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        # ---- Setup ----
        section("Setup")
        r = c.get(HOMEPAGE)
        homepage_html = r.text
        save("homepage.html", homepage_html)
        print(f"  homepage status: {r.status_code}, size {len(homepage_html)}")

        # ============================================================
        # A) ShoppingContext input shape
        # ============================================================
        section("A) ShoppingContext: probe input field names")
        # If we pass an empty object, the server says which required fields are missing.
        # Then we try adding common fields one at a time. Each unknown field is leaked.
        # Start with empty object:
        q = '''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {
          productDetail(storeId: $s, id: $id, shoppingContext: $ctx) { __typename }
        }'''
        r = post_query(c, q, {"s": STORE_ID, "id": "1510154", "ctx": {}})
        print(f"  ctx={{}} status={r.status_code}")
        try:
            errs = r.json().get("errors", [])
            for e in errs[:10]:
                print(f"    err: {e.get('message', '')[:240]}")
        except Exception:
            print(f"    body: {r.text[:400]}")
        save("ctx_empty.json", r.text)
        time.sleep(0.4)

        # Probe candidate fields one at a time. Unknown ones leak via
        # "Field 'foo' is not defined by type 'ShoppingContext'".
        ctx_field_candidates = [
            "storeId", "fulfillmentType", "fulfillmentChannel", "channel",
            "deliveryType", "shoppingMode", "type", "mode",
            "zipCode", "zip", "postalCode",
            "lat", "lng", "latitude", "longitude",
            "customerId", "userId", "sessionId",
            "preferredStore", "selectedStore", "store",
            "isCurbside", "isDelivery", "isInStore",
            "shopType", "shopperType", "orderType", "orderingMethod",
            "pickup", "delivery", "shipping",
        ]
        # Use one shot with all the fields = wrong, then take the errors.
        all_fields_obj = {f: "x" for f in ctx_field_candidates}
        r = post_query(c, q, {"s": STORE_ID, "id": "1510154", "ctx": all_fields_obj})
        save("ctx_all_fields.json", r.text)
        print(f"\n  posted all candidates; errors received:")
        try:
            errs = r.json().get("errors", [])
            unknown_fields = set()
            known_fields = set(ctx_field_candidates)
            for e in errs:
                msg = e.get("message", "")
                m = re.search(r"Field [\"']?(\w+)[\"']? is not defined by type [\"']?ShoppingContext", msg)
                if m:
                    unknown_fields.add(m.group(1))
                    continue
                m = re.search(r"of type [\"']?ShoppingContext[\"']?, field [\"']?(\w+)[\"']?", msg)
                if m:
                    unknown_fields.add(m.group(1))
                    continue
                print(f"    other err: {msg[:240]}")
            valid_fields = known_fields - unknown_fields
            print(f"\n  unknown fields (leaked): {sorted(unknown_fields)}")
            print(f"  presumably valid fields:  {sorted(valid_fields)}")
            save("ctx_field_analysis.json", {
                "unknown": sorted(unknown_fields),
                "valid_candidates": sorted(valid_fields),
            })
        except Exception as e:
            print(f"  parse error: {e}")

        # Now try a likely-minimal context: { storeId, fulfillmentChannel }
        section("A2) ShoppingContext: try guessed minimal shape")
        guesses = [
            {"storeId": STORE_ID, "fulfillmentChannel": "PICKUP"},
            {"storeId": STORE_ID, "fulfillmentType": "PICKUP"},
            {"storeId": STORE_ID, "channel": "PICKUP"},
            {"storeId": STORE_ID, "shoppingMode": "PICKUP"},
            {"storeId": STORE_ID, "fulfillmentChannel": "CURBSIDE"},
            {"storeId": STORE_ID, "fulfillmentChannel": "IN_STORE"},
            {"storeId": STORE_ID},
        ]
        for g in guesses:
            r = post_query(c, q, {"s": STORE_ID, "id": "1510154", "ctx": g})
            print(f"  ctx={g}")
            print(f"    status={r.status_code} err={err_msg(r)[:260]}")
            # If we get data, save it specially
            try:
                body = r.json()
                if body.get("data") is not None:
                    save("PRODUCT_DETAIL_SUCCESS.json", body)
                    print(f"    *** got data field! saved to PRODUCT_DETAIL_SUCCESS.json")
            except Exception:
                pass
            time.sleep(0.4)

        # ============================================================
        # B) store(storeNumber: 92) and Store fields
        # ============================================================
        section("B) store(storeNumber: 92): verify works + probe Store fields")
        q = 'query Q($n: Int!) { store(storeNumber: $n) { __typename } }'
        r = post_query(c, q, {"n": int(STORE_ID)})
        print(f"  store(92) with __typename: status={r.status_code}")
        print(f"    body: {r.text[:400]}")
        save("store_92_typename.json", r.text)
        time.sleep(0.4)

        # If that succeeded, probe Store fields
        store_field_candidates = [
            "id", "storeNumber", "number", "name", "displayName",
            "address1", "address2", "address", "addressLine1",
            "city", "state", "zip", "zipCode", "postalCode",
            "phone", "phoneNumber", "hours", "operatingHours",
            "latitude", "longitude", "lat", "lng",
            "services", "departments", "amenities",
            "isOpen", "open", "status",
        ]
        for f in store_field_candidates:
            q = f'query Q($n: Int!) {{ store(storeNumber: $n) {{ {f} }} }}'
            r = post_query(c, q, {"n": int(STORE_ID)})
            try:
                body = r.json()
                if body.get("data") and body["data"].get("store"):
                    val = body["data"]["store"].get(f)
                    print(f"  field={f:18s} VALID -> {str(val)[:80]}")
                    save(f"store_field_{f}.json", body)
                else:
                    em = err_msg(r)
                    print(f"  field={f:18s} ERR -> {em[:140]}")
            except Exception as e:
                print(f"  field={f:18s} parse error: {e}")
            time.sleep(0.3)

        # ============================================================
        # C) Find stores-by-zip operation
        # ============================================================
        section("C) Find store-by-location operation")
        store_loc_ops = [
            "storesByZip", "storesByPostalCode", "storesByCity",
            "nearbyStores", "storesNearMe", "findStore",
            "storeSearch", "searchStores", "storeQuery",
            "storesByAddress", "storesByLocation",
            "deliveryStores", "pickupStores", "curbsideStores",
        ]
        for op in store_loc_ops:
            for var_shape in [
                ("zip", '$v: String!', '"78418"'),
                ("query", '$v: String!', '"78418"'),
            ]:
                arg_name, var_decl, val_lit = var_shape
                q = f'query Q({var_decl}) {{ {op}({arg_name}: $v) {{ __typename }} }}'
                r = post_query(c, q, {"v": "78418"})
                em = err_msg(r)
                # If we get "Cannot query field X on type Query" — op doesn't exist.
                # If we get anything else — op MIGHT exist.
                if "Cannot query field" in em and op in em:
                    # confirmed nonexistent
                    pass
                else:
                    print(f"  {op}({arg_name}:) -> status={r.status_code} err={em[:200]}")
                time.sleep(0.2)

        # ============================================================
        # D) Download MORE JS chunks (bigger ones) and grep harder
        # ============================================================
        section("D) Download more JS chunks + harder grep")
        js_urls = re.findall(r'<script[^>]+src="([^"]+\.js[^"]*)"', homepage_html)
        cx_urls = [u for u in js_urls if "cx.static.heb.com" in u]
        print(f"  total cx chunks available: {len(cx_urls)}")

        # First, check sizes via HEAD to find the biggest chunks
        # (skip HEAD to save time; just download all in batches)
        chunks_to_fetch = cx_urls[:40]  # was 15
        print(f"  fetching {len(chunks_to_fetch)} chunks...")

        all_operations = set()
        productDetail_snippets = []
        ShoppingContext_snippets = []
        store_snippets = []
        productSearch_snippets = []
        browseCategory_snippets = []
        gql_query_strings = []

        for i, url in enumerate(chunks_to_fetch):
            try:
                r = c.get(url, timeout=20.0)
                if r.status_code != 200:
                    continue
                js = r.text
                short = url.split("/")[-1]

                # Operation names embedded as strings
                ops = set()
                for pat in [
                    r'(?:query|mutation)\s+([A-Z]\w+)',
                    r'operationName\s*[:=]\s*["\']([A-Za-z]\w+)["\']',
                    r'["\']operationName["\']\s*:\s*["\']([A-Za-z]\w+)["\']',
                ]:
                    ops.update(re.findall(pat, js))
                if ops:
                    all_operations.update(ops)
                    if len(ops) > 2:
                        print(f"    [{i:2d}] {short[:55]:55s} {len(ops):3d} ops")

                # Capture context around key terms
                for needle, bucket in [
                    ("productDetail", productDetail_snippets),
                    ("ShoppingContext", ShoppingContext_snippets),
                    ("store(", store_snippets),
                    ("productSearch", productSearch_snippets),
                    ("browseCategory", browseCategory_snippets),
                ]:
                    for m in re.finditer(re.escape(needle) + r"[^`]{0,600}", js):
                        snippet = m.group(0)
                        if len(snippet) > 50:
                            bucket.append({"chunk": short, "snippet": snippet[:600]})

                # Find gql template literal content: `query Foo(...) { ... }`
                # Apollo strings start with `query` or `mutation` and have balanced braces.
                # We look for any string-literal containing "query Xxx" plus "{".
                for m in re.finditer(r'["\`]((?:query|mutation)\s+\w+[^"`]{20,4000})["\`]', js):
                    gql_query_strings.append({"chunk": short, "body": m.group(1)[:2000]})

            except Exception as e:
                print(f"    ERROR {url}: {e}")

        print(f"\n  Total unique operations: {len(all_operations)}")
        for op in sorted(all_operations):
            print(f"    {op}")
        save("operations_found.json", sorted(all_operations))

        save("snippets_productDetail.json", productDetail_snippets[:50])
        save("snippets_ShoppingContext.json", ShoppingContext_snippets[:50])
        save("snippets_store.json", store_snippets[:50])
        save("snippets_productSearch.json", productSearch_snippets[:50])
        save("snippets_browseCategory.json", browseCategory_snippets[:50])
        save("gql_query_strings.json", gql_query_strings[:100])

        print(f"\n  snippet counts:")
        print(f"    productDetail: {len(productDetail_snippets)}")
        print(f"    ShoppingContext: {len(ShoppingContext_snippets)}")
        print(f"    store(: {len(store_snippets)}")
        print(f"    productSearch: {len(productSearch_snippets)}")
        print(f"    browseCategory: {len(browseCategory_snippets)}")
        print(f"    full gql template strings: {len(gql_query_strings)}")

        # Print a sampling of the most useful snippets directly
        if ShoppingContext_snippets:
            print(f"\n  --- first ShoppingContext snippets ---")
            for s in ShoppingContext_snippets[:5]:
                print(f"    [{s['chunk']}] {s['snippet'][:300]}")
        if productDetail_snippets:
            print(f"\n  --- first productDetail snippets ---")
            for s in productDetail_snippets[:5]:
                print(f"    [{s['chunk']}] {s['snippet'][:300]}")
        if gql_query_strings:
            print(f"\n  --- first full gql strings ---")
            for s in gql_query_strings[:5]:
                print(f"    [{s['chunk']}] {s['body'][:400]}")

    print(f"\nDone. Output in {OUTDIR}")


if __name__ == "__main__":
    main()
