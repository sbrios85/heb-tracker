"""
H-E-B GraphQL Probe — Phase 14
------------------------------
Phase 13 finding: "productDetail" in JS chunks refers only to analytics events,
NOT a GraphQL operation. The schema uses "productDetails" (plural).

But our queries against productDetails would error with "Cannot query field
productDetails on type Query" — so SOMETHING else is the right operation.

Key insight: chunks loaded by the homepage don't contain the product-detail-page
code. Next.js code-splits by route. We need to fetch a real product page and
collect ITS JS chunks (which will contain the product detail query).

Plan:
  1. Fetch a real product page: /product-detail/.../583162
  2. Extract its JS chunks (will include the PDP-specific ones)
  3. Search those chunks for actual GraphQL query bodies
  4. Print every gql template literal we find
"""

import json
import re
import time
from pathlib import Path
import httpx

PRODUCT_URL = "https://www.heb.com/product-detail/cafe-ol-by-h-e-b-texas-pecan-medium-roast-ground-coffee/583162"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
OUTDIR = Path(__file__).parent.parent / "probe_output"
OUTDIR.mkdir(exist_ok=True)


def safe(s):
    return re.sub(r'[^A-Za-z0-9_\-.]', '_', str(s))[:80]


def save(name, data):
    p = OUTDIR / safe(name)
    p.write_text(json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data))


def section(t):
    print(f"\n{'=' * 70}\n  {t}\n{'=' * 70}")


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        # Seed cookies
        c.get("https://www.heb.com/")

        section(f"Fetch product page: {PRODUCT_URL}")
        r = c.get(PRODUCT_URL)
        pdp_html = r.text
        print(f"  status: {r.status_code}, size: {len(pdp_html)}")
        save("phase14_pdp.html", pdp_html)

        # Extract JS chunks referenced by the PDP
        js_urls = re.findall(r'<script[^>]+src="([^"]+\.js[^"]*)"', pdp_html)
        cx_urls = [u for u in js_urls if "cx.static.heb.com" in u]
        print(f"  found {len(cx_urls)} cx.static.heb.com chunks on PDP")
        save("phase14_pdp_chunks.json", cx_urls)

        # Also extract __NEXT_DATA__ which on a product page should contain
        # the productDetail query result already prefetched
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', pdp_html, re.DOTALL)
        if m:
            print(f"  __NEXT_DATA__ found, size: {len(m.group(1))}")
            try:
                nd = json.loads(m.group(1))
                save("phase14_pdp_next_data.json", nd)
                # Look for product-shaped data anywhere in it
                def walk(obj, path=""):
                    hits = []
                    if isinstance(obj, dict):
                        keys = set(obj.keys())
                        # Spot product-shaped objects
                        product_keys = {"id", "displayName", "price", "image", "brand"}
                        if len(keys & product_keys) >= 2:
                            hits.append({"path": path, "keys": sorted(keys)[:30], "sample": json.dumps(obj)[:600]})
                        for k, v in obj.items():
                            hits.extend(walk(v, f"{path}.{k}"))
                    elif isinstance(obj, list):
                        for i, v in enumerate(obj[:3]):  # sample first 3
                            hits.extend(walk(v, f"{path}[{i}]"))
                    return hits
                product_hits = walk(nd)
                print(f"  product-shaped objects found in __NEXT_DATA__: {len(product_hits)}")
                for h in product_hits[:5]:
                    print(f"\n    PATH: {h['path']}")
                    print(f"    KEYS: {h['keys']}")
                    print(f"    SAMPLE: {h['sample'][:500]}")
                save("phase14_product_hits.json", product_hits[:30])
            except json.JSONDecodeError as e:
                print(f"  __NEXT_DATA__ parse error: {e}")
        else:
            print("  no __NEXT_DATA__ found")

        # Now grep PDP chunks for query bodies
        section("Grep PDP JS chunks for actual GraphQL query bodies")
        all_queries = []
        for i, url in enumerate(cx_urls):
            try:
                r = c.get(url, timeout=30.0)
                if r.status_code != 200:
                    continue
                js = r.text
                short = url.split("/")[-1]

                # Apollo compiled queries appear as JS template literal strings
                # split by commas inside arrays. Look for any string starting with
                # "\n  query" or "\n  mutation".
                for m in re.finditer(r'"(\\n\s*(?:query|mutation)\s+\w+[^"]{50,5000})"', js):
                    body = m.group(1).replace("\\n", "\n")
                    # Get operation name
                    op_match = re.search(r'(?:query|mutation)\s+(\w+)', body)
                    op = op_match.group(1) if op_match else "?"
                    all_queries.append({"chunk": short, "operation": op, "body": body})

                # Also look for shorter Apollo strings spread inside arrays:
                # ["\n  query Foo(...) {\n    ...\n  }\n", "\n", "\n"]
                for m in re.finditer(r'\[\s*"(\\n\s*(?:query|mutation)\s+\w+[^"]{50,5000})"', js):
                    body = m.group(1).replace("\\n", "\n")
                    op_match = re.search(r'(?:query|mutation)\s+(\w+)', body)
                    op = op_match.group(1) if op_match else "?"
                    all_queries.append({"chunk": short, "operation": op, "body": body, "array_form": True})

                ops_in_chunk = set(q["operation"] for q in all_queries if q["chunk"] == short)
                if ops_in_chunk:
                    print(f"  [{i:2d}] {short[:55]:55s} ops: {sorted(ops_in_chunk)[:8]}")
            except Exception as e:
                print(f"  err {url}: {e}")
            time.sleep(0.15)

        save("phase14_all_queries.json", all_queries)

        # Find PRODUCT-related queries
        section("Product-related queries discovered")
        product_queries = [q for q in all_queries if "product" in q["body"].lower() or "Product" in q["operation"]]
        # Dedupe by operation+chunk
        seen = set()
        unique = []
        for q in product_queries:
            key = (q["operation"], q["chunk"])
            if key not in seen:
                seen.add(key)
                unique.append(q)

        print(f"  total product-related: {len(product_queries)}, unique by op+chunk: {len(unique)}")
        for q in unique:
            print(f"\n  === {q['operation']} (from {q['chunk']}) ===")
            print(q["body"][:2500])

    print(f"\nDone.")


if __name__ == "__main__":
    main()
