"""
H-E-B GraphQL Probe — Phase 3
-----------------------------
Phase 2 discoveries:
  - productDetail REQUIRES storeId: ID! (plus one more arg, name unknown)
  - Query.store(...) exists (returned 200 "No Store Found")
  - Frontend is Next.js with __NEXT_DATA__ blob (initial state in HTML)
  - 50 JS chunks at cx.static.heb.com/_next/static/chunks/

Phase 3 goals:
  A) Extract a default storeId from __NEXT_DATA__ (Corpus Christi if possible).
  B) With storeId in hand, find productDetail's second argument name.
  C) Find Query.store's required argument(s).
  D) Download key JS chunks and grep for GraphQL query strings + operation names.
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
    if variables:
        payload["variables"] = variables
    if op_name:
        payload["operationName"] = op_name
    return client.post(ENDPOINT, json=payload)


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        # ----- Setup -----
        section("Setup: homepage + __NEXT_DATA__")
        r = c.get(HOMEPAGE)
        homepage_html = r.text
        save("homepage.html", homepage_html)
        print(f"  homepage status: {r.status_code}, size {len(homepage_html)}")

        next_match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            homepage_html, re.DOTALL,
        )
        store_id = None
        if next_match:
            blob = next_match.group(1)
            try:
                nd = json.loads(blob)
                save("next_data.json", nd)
                # Walk the structure looking for store-related keys.
                # Print top-level shape first.
                print("  __NEXT_DATA__ top-level keys:", list(nd.keys()))
                if "props" in nd:
                    print("  props keys:", list(nd["props"].keys()) if isinstance(nd["props"], dict) else type(nd["props"]))
                # Try to find storeId by deep search
                def deep_find(obj, key_pattern, path=""):
                    found = []
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if re.search(key_pattern, k, re.I):
                                found.append((f"{path}.{k}", v))
                            found.extend(deep_find(v, key_pattern, f"{path}.{k}"))
                    elif isinstance(obj, list):
                        for i, v in enumerate(obj):
                            found.extend(deep_find(v, key_pattern, f"{path}[{i}]"))
                    return found

                store_hits = deep_find(nd, r"^store(Id|Number|Num|Code)?$")
                print(f"  store-related hits in __NEXT_DATA__: {len(store_hits)}")
                for path, val in store_hits[:25]:
                    val_repr = json.dumps(val)[:120] if not isinstance(val, (dict, list)) else f"<{type(val).__name__} len={len(val) if hasattr(val,'__len__') else '?'}>"
                    print(f"    {path} = {val_repr}")
                save("store_hits.json", [(p, v if isinstance(v, (str, int, float, bool, type(None))) else str(type(v))) for p, v in store_hits])

                # Try to find first scalar storeId-ish value
                for path, val in store_hits:
                    if isinstance(val, (str, int)) and str(val).strip() and not isinstance(val, bool):
                        store_id = str(val)
                        print(f"  ===> using store_id = {store_id} (from {path})")
                        break
            except json.JSONDecodeError as e:
                print(f"  __NEXT_DATA__ JSON parse failed: {e}")
                save("next_data_raw.txt", blob[:5000])
        else:
            print("  no __NEXT_DATA__ found")

        if not store_id:
            # Fallback: try a few common defaults. H-E-B store numbers are 3-4 digits.
            # The Waldron Rd store number is typically in the 500-600 range for Corpus.
            store_id = "1"
            print(f"  no store_id discovered; will probe with fallback '{store_id}'")

        # ----- A) productDetail: find the second required arg -----
        section(f"A) productDetail: probe second-arg names with storeId={store_id}")
        # We KNOW storeId is required. Now find the product identifier arg.
        prod_arg_candidates = [
            "id", "productId", "productID", "sku", "upc", "ean",
            "code", "productCode", "slug", "key", "uid", "pid",
            "itemId", "itemNumber", "productNumber",
        ]
        for arg in prod_arg_candidates:
            q = f'''query Q($s: ID!, $v: String!) {{
                productDetail(storeId: $s, {arg}: $v) {{ __typename }}
            }}'''
            r = post_query(c, q, {"s": store_id, "v": "1510154"})
            try:
                err = r.json()["errors"][0]["message"]
            except Exception:
                err = r.text[:200]
            success = r.status_code == 200 and "errors" not in r.text
            marker = "SUCCESS" if success else "ERR"
            print(f"  arg={arg:20s} status={r.status_code} {marker:8s} err={err[:170]}")
            save(f"pd_arg_{arg}.json", r.text)
            time.sleep(0.4)

        # ----- B) productDetail: maybe storeId alone is enough -----
        section("B) productDetail: try storeId-only")
        q = f'query Q($s: ID!) {{ productDetail(storeId: $s) {{ __typename }} }}'
        r = post_query(c, q, {"s": store_id})
        print(f"  status={r.status_code} body={r.text[:400]}")
        save("pd_storeid_only.json", r.text)
        time.sleep(0.4)

        # ----- C) Query.store argument names -----
        section("C) Query.store: probe argument names")
        store_arg_candidates = [
            "id", "storeId", "storeNumber", "number",
            "zipCode", "zip", "postalCode", "address",
            "lat", "latitude", "code",
        ]
        for arg in store_arg_candidates:
            # Use sensible value per arg
            val = store_id if arg in ("id", "storeId", "storeNumber", "number", "code") else "78418"
            q = f'query Q($v: String!) {{ store({arg}: $v) {{ __typename }} }}'
            r = post_query(c, q, {"v": val})
            try:
                err = r.json()["errors"][0]["message"]
            except Exception:
                err = r.text[:200]
            success = r.status_code == 200 and "errors" not in r.text
            marker = "SUCCESS" if success else "ERR"
            print(f"  arg={arg:18s} val={val:8s} status={r.status_code} {marker:8s} err={err[:160]}")
            save(f"store_arg_{arg}.json", r.text)
            time.sleep(0.4)

        # Also try with no args at all and with __typename
        section("C2) Query.store with no args")
        q = '{ store { __typename } }'
        r = post_query(c, q)
        print(f"  status={r.status_code} body={r.text[:400]}")
        save("store_noarg.json", r.text)

        # ----- D) Download a few JS chunks and grep for GraphQL strings -----
        section("D) Download Next.js JS chunks and grep for GraphQL operations")
        # We pull a sample of the larger chunks. The main chunks contain
        # query strings and operation names.
        js_urls = re.findall(r'<script[^>]+src="([^"]+\.js[^"]*)"', homepage_html)
        chunks_to_fetch = [u for u in js_urls if "cx.static.heb.com" in u][:15]
        print(f"  fetching {len(chunks_to_fetch)} chunks...")

        all_operations = set()
        all_query_starts = []

        for url in chunks_to_fetch:
            try:
                r = c.get(url, timeout=20.0)
                if r.status_code != 200:
                    print(f"    {url} -> {r.status_code}")
                    continue
                js = r.text
                # Pattern 1: GraphQL query strings often start with "query Foo("
                # or "mutation Foo(". They're embedded as JS string literals.
                queries = re.findall(
                    r'(?:query|mutation)\s+([A-Z]\w+)\s*[(\{]',
                    js,
                )
                # Pattern 2: Apollo `gql\`query ...\``
                gql_blocks = re.findall(
                    r'(?:query|mutation)\s+([A-Z]\w+)[^"\']{0,500}',
                    js,
                )
                # Pattern 3: operationName references in mapping objects
                op_refs = re.findall(
                    r'operationName\s*[:=]\s*["\']([A-Za-z][\w]+)["\']',
                    js,
                )
                ops_here = set(queries + gql_blocks + op_refs)
                if ops_here:
                    all_operations.update(ops_here)
                    short = url.split("/")[-1]
                    print(f"    {short:60s} -> {len(ops_here)} ops")

                # Capture query-shape snippets for productDetail and store
                for snippet_pattern in ["productDetail", "Query.store", "store(", "productSearch", "category", "browse"]:
                    for m in re.finditer(re.escape(snippet_pattern) + r"[^`'\"]{0,400}", js):
                        all_query_starts.append((url.split("/")[-1], snippet_pattern, m.group(0)[:400]))
            except Exception as e:
                print(f"    {url} -> ERROR {e}")

        print(f"\n  Total unique operations found: {len(all_operations)}")
        for op in sorted(all_operations)[:60]:
            print(f"    {op}")
        save("operations_found.json", sorted(all_operations))

        # Save snippets containing key terms
        snippets_grouped = {}
        for chunk_name, pattern, snippet in all_query_starts:
            snippets_grouped.setdefault(pattern, []).append({"chunk": chunk_name, "snippet": snippet})
        save("query_snippets.json", snippets_grouped)
        print(f"\n  Snippet groups: " + ", ".join(f"{k}={len(v)}" for k, v in snippets_grouped.items()))

    print(f"\nDone. Output in {OUTDIR}")


if __name__ == "__main__":
    main()
