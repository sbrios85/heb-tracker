"""
Probe v2: crack updatePreferredStore.

Phase 1 findings:
  - updatePreferredStore(storeNumber: Int) exists, returns null, no error
  - Setting arbitrary cookies makes product pages return storeId=None (breaks something)
  - Query params don't work

Phase 2:
  A) Discover updatePreferredStore's return type + required selection set
  B) Run the mutation on a client, inspect what cookies it sets, then fetch
     product on the SAME client (carry session forward)
  C) Check if there's a 'setStore' / store-context header instead
  D) Inspect the homepage __NEXT_DATA__ for how storeId=92 gets set (maybe
     there's a geolocation/IP default we can override with a header)
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
TARGET = 57


def section(t):
    print(f"\n{'=' * 70}\n  {t}\n{'=' * 70}")


def fresh_client():
    c = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0)
    c.get(HOMEPAGE)
    return c


def get_store(client):
    url = f"https://www.heb.com/product-detail/{TEST_SLUG}/{TEST_PRODUCT_ID}"
    r = client.get(url)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
    if not m:
        return None, f"no NEXT_DATA (status {r.status_code})"
    try:
        nd = json.loads(m.group(1))
        product = (nd.get("props") or {}).get("pageProps", {}).get("product")
        if product is None:
            # what IS in pageProps?
            pp = (nd.get("props") or {}).get("pageProps", {})
            return None, f"no product; pageProps keys={list(pp.keys())[:10]}"
        return product.get("storeId"), "ok"
    except Exception as e:
        return None, str(e)


def main():
    # ============================================================
    # A) updatePreferredStore return type discovery
    # ============================================================
    section("A) updatePreferredStore return-type probe")
    c = fresh_client()
    # Try selection sets to find the type
    selections = [
        "__typename",
        "__typename ... on Store { storeNumber name }",
        "__typename ... on PreferredStore { storeNumber }",
        "__typename ... on UpdatePreferredStoreResult { __typename }",
        "__typename ... on UserMessage { message code }",
    ]
    for sel in selections:
        mut = f"mutation M($n: Int!) {{ updatePreferredStore(storeNumber: $n) {{ {sel} }} }}"
        try:
            r = c.post(GRAPHQL_ENDPOINT, json={"query": mut, "variables": {"n": TARGET}})
            body = r.json()
            if "errors" in body:
                print(f"  sel='{sel[:40]}' ERR: {body['errors'][0]['message'][:120]}")
            else:
                print(f"  sel='{sel[:40]}' OK: {json.dumps(body)[:200]}")
        except Exception as e:
            print(f"  sel='{sel[:40]}' EXC: {e}")

    # ============================================================
    # B) Mutation then same-session product fetch + cookie inspection
    # ============================================================
    section("B) Mutation on client, inspect cookies, fetch product SAME session")
    c = fresh_client()
    cookies_before = dict(c.cookies)
    mut = "mutation M($n: Int!) { updatePreferredStore(storeNumber: $n) { __typename } }"
    r = c.post(GRAPHQL_ENDPOINT, json={"query": mut, "variables": {"n": TARGET}})
    print(f"  mutation response: {r.text[:200]}")
    print(f"  set-cookie header: {r.headers.get('set-cookie', '(none)')[:300]}")
    cookies_after = dict(c.cookies)
    new = {k: v for k, v in cookies_after.items() if k not in cookies_before}
    print(f"  new cookies from mutation: {new}")
    # Now fetch product on SAME client
    store, status = get_store(c)
    marker = "✓✓✓" if store == TARGET else ""
    print(f"  product storeId after mutation (same session): {store} ({status}) {marker}")

    # ============================================================
    # C) Try a store-context HTTP header on the product fetch
    # ============================================================
    section("C) Store-context HTTP headers")
    header_names = [
        "x-heb-store", "x-store-id", "x-store-number", "heb-store-id",
        "x-preferred-store", "store-id", "x-heb-store-id",
    ]
    for hname in header_names:
        c = fresh_client()
        url = f"https://www.heb.com/product-detail/{TEST_SLUG}/{TEST_PRODUCT_ID}"
        r = c.get(url, headers={hname: str(TARGET)})
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
        store = None
        if m:
            try:
                nd = json.loads(m.group(1))
                store = (nd.get("props") or {}).get("pageProps", {}).get("product", {}).get("storeId")
            except Exception:
                pass
        marker = "✓✓✓" if store == TARGET else ""
        print(f"  header {hname}={TARGET} -> storeId={store} {marker}")

    # ============================================================
    # D) How is storeId=92 chosen? Inspect homepage NEXT_DATA for store context
    # ============================================================
    section("D) Homepage __NEXT_DATA__ store-context inspection")
    c = fresh_client()
    r = c.get(HOMEPAGE)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
    if m:
        try:
            nd = json.loads(m.group(1))
            def walk(o, p=""):
                hits = []
                if isinstance(o, dict):
                    for k, v in o.items():
                        if re.search(r'store(Id|Number|Context)|fulfillment|defaultStore', k, re.I):
                            if isinstance(v, (int, str, bool)):
                                hits.append((f"{p}.{k}", v))
                            elif isinstance(v, dict):
                                hits.append((f"{p}.{k}", f"<dict keys={list(v.keys())[:8]}>"))
                        hits.extend(walk(v, f"{p}.{k}"))
                elif isinstance(o, list):
                    for i, v in enumerate(o[:2]):
                        hits.extend(walk(v, f"{p}[{i}]"))
                return hits
            hits = walk(nd)
            print(f"  store-context fields in homepage NEXT_DATA ({len(hits)}):")
            for path, val in hits[:30]:
                print(f"    {path} = {val}")
        except Exception as e:
            print(f"  parse error: {e}")

    # ============================================================
    # E) Check the GraphQL request the frontend uses — does productDetails
    #    take a storeId we can override? We have productSearch working with
    #    storeId=57. Maybe we fetch details via GraphQL instead of the page.
    # ============================================================
    section("E) Can we get price via GraphQL productDetails with storeId=57?")
    # We know productSearch(storeId:57) works. The product page uses a
    # different query. Let's see if there's a getProductById we saw earlier:
    #   query productPrefsForProduct { product: getProductById(id, storeId) }
    c = fresh_client()
    q = """query Q($id: String!, $storeId: String) {
      getProductById(id: $id, storeId: $storeId) {
        id
        __typename
      }
    }"""
    r = c.post(GRAPHQL_ENDPOINT, json={"query": q, "variables": {"id": TEST_PRODUCT_ID, "storeId": str(TARGET)}})
    print(f"  getProductById: {r.text[:300]}")


if __name__ == "__main__":
    main()
