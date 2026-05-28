"""
Probe: how to pin the store to #57 (Waldron) so product pages return
Waldron pricing instead of the default Victoria (92).

We test multiple mechanisms and after each, fetch a known product page
and check the returned storeId.
"""

import json
import re
import sys
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import HEADERS, HOMEPAGE, GRAPHQL_ENDPOINT

TEST_PRODUCT_ID = "583162"
TEST_SLUG = "cafe-ol-by-h-e-b-texas-pecan-medium-roast-ground-coffee"
TARGET_STORE = 57


def section(t):
    print(f"\n{'=' * 70}\n  {t}\n{'=' * 70}")


def get_product_store(client):
    """Fetch the test product and return its storeId from __NEXT_DATA__."""
    url = f"https://www.heb.com/product-detail/{TEST_SLUG}/{TEST_PRODUCT_ID}"
    r = client.get(url)
    if r.status_code != 200:
        return None, f"status {r.status_code}"
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
    if not m:
        return None, "no __NEXT_DATA__"
    try:
        nd = json.loads(m.group(1))
        product = (nd.get("props") or {}).get("pageProps", {}).get("product")
        if product:
            return product.get("storeId"), "ok"
        # maybe store is elsewhere
        return None, "no product in NEXT_DATA"
    except json.JSONDecodeError as e:
        return None, f"parse error {e}"


def fresh_client():
    c = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0)
    c.get(HOMEPAGE)
    return c


def main():
    # Baseline: no store setting
    section("Baseline (no store set)")
    c = fresh_client()
    store, status = get_product_store(c)
    print(f"  storeId={store} ({status})")
    print(f"  cookies: {list(c.cookies.keys())}")

    # ---- Approach A: query param ?storeId=57 on the product URL ----
    section("A) Query param variations on product URL")
    for param in ["storeId", "store", "storeNumber", "selectedStore"]:
        c = fresh_client()
        url = f"https://www.heb.com/product-detail/{TEST_SLUG}/{TEST_PRODUCT_ID}?{param}={TARGET_STORE}"
        r = c.get(url)
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
        store = None
        if m:
            try:
                nd = json.loads(m.group(1))
                store = (nd.get("props") or {}).get("pageProps", {}).get("product", {}).get("storeId")
            except Exception:
                pass
        marker = "✓✓✓" if store == TARGET_STORE else ""
        print(f"  ?{param}={TARGET_STORE} -> storeId={store} {marker}")

    # ---- Approach B: various cookie names ----
    section("B) Cookie variations")
    cookie_names = [
        "HEB_PREFERRED_STORE", "preferredStore", "storeNumber", "storeId",
        "CURR_SESSION_STORE", "selected_store", "userStore",
        "HEB_STORE", "store_id", "currentStore", "fulfillmentStoreId",
    ]
    for cname in cookie_names:
        c = fresh_client()
        c.cookies.set(cname, str(TARGET_STORE), domain=".heb.com")
        store, status = get_product_store(c)
        marker = "✓✓✓" if store == TARGET_STORE else ""
        print(f"  cookie {cname}={TARGET_STORE} -> storeId={store} {marker}")

    # ---- Approach C: GraphQL mutations to set store ----
    section("C) GraphQL mutation variations")
    mutations = [
        ("updatePreferredStore Int",
         "mutation M($n: Int!) { updatePreferredStore(storeNumber: $n) { __typename } }",
         {"n": TARGET_STORE}),
        ("updatePreferredStore storeId",
         "mutation M($n: Int!) { updatePreferredStore(storeId: $n) { __typename } }",
         {"n": TARGET_STORE}),
        ("setPreferredStore",
         "mutation M($n: Int!) { setPreferredStore(storeNumber: $n) { __typename } }",
         {"n": TARGET_STORE}),
        ("selectStore",
         "mutation M($n: Int!) { selectStore(storeNumber: $n) { __typename } }",
         {"n": TARGET_STORE}),
    ]
    for name, mut, vars_ in mutations:
        c = fresh_client()
        try:
            r = c.post(GRAPHQL_ENDPOINT, json={"query": mut, "variables": vars_})
            body = r.json()
            if "errors" in body:
                err = body["errors"][0]["message"][:80]
                print(f"  {name}: mutation err: {err}")
                continue
            else:
                print(f"  {name}: mutation OK -> {json.dumps(body)[:150]}")
        except Exception as e:
            print(f"  {name}: exception {e}")
            continue
        # Now check if the product page reflects it
        store, status = get_product_store(c)
        marker = "✓✓✓" if store == TARGET_STORE else ""
        print(f"     after mutation -> storeId={store} {marker}")

    # ---- Approach D: look at what cookies the site sets when we visit a store page ----
    section("D) Visit the store page and capture cookies")
    c = fresh_client()
    before = set(c.cookies.keys())
    store_page = f"https://www.heb.com/heb-store/tx/corpus-christi/flour-bluff-h-e-b-plus--{TARGET_STORE}"
    r = c.get(store_page)
    print(f"  store page status: {r.status_code}")
    after = set(c.cookies.keys())
    new_cookies = after - before
    print(f"  new cookies after visiting store page: {new_cookies}")
    for k in c.cookies.keys():
        v = c.cookies.get(k)
        # Only print short values (store-id-like)
        if v and len(str(v)) < 20:
            print(f"    {k} = {v}")
    # Check product store now
    store, status = get_product_store(c)
    marker = "✓✓✓" if store == TARGET_STORE else ""
    print(f"  after store-page visit -> storeId={store} {marker}")

    # ---- Approach E: combine store-page visit THEN check ----
    section("E) Look for a 'set store' or 'curbside' action endpoint in store page HTML")
    # Search the store page HTML for any setStore / selectStore / storeId references
    setters = re.findall(r'(set[A-Z]\w*[Ss]tore\w*|select[A-Z]?\w*[Ss]tore\w*)', r.text)
    print(f"  store-setter-like tokens in store page: {set(setters)}")
    # Look for fulfillment/store IDs in the store page __NEXT_DATA__
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
    if m:
        try:
            nd = json.loads(m.group(1))
            # find storeId / storeNumber fields
            def walk(o, p=""):
                hits = []
                if isinstance(o, dict):
                    for k, v in o.items():
                        if re.search(r'store(Id|Number)', k, re.I) and isinstance(v, (int, str)):
                            hits.append((f"{p}.{k}", v))
                        hits.extend(walk(v, f"{p}.{k}"))
                elif isinstance(o, list):
                    for i, v in enumerate(o[:3]):
                        hits.extend(walk(v, f"{p}[{i}]"))
                return hits
            for path, val in walk(nd)[:20]:
                print(f"    {path} = {val}")
        except Exception as e:
            print(f"  parse error: {e}")


if __name__ == "__main__":
    main()
