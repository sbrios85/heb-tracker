"""
Quick probe: find the actual queryable top-level categoryIds.

Strategy:
1. Test ShopNavigation operation if it exists.
2. Otherwise scrape the homepage HTML for /category/shop/{slug}/{id1}/{id2} links.
3. Test each candidate via browseCategory to see which return data.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import make_client, gql, browse_category, save_json, polite_sleep, WALDRON_STORE_NUMBER, SHOPPING_CONTEXT, HOMEPAGE


def main():
    c = make_client()

    # ============================================================
    # 1) Try ShopNavigation
    # ============================================================
    print("=" * 70)
    print("  Try ShopNavigation operation")
    print("=" * 70)
    attempts = [
        ('{ shopNavigation { __typename } }', None),
        ('query Q($s: Int!) { shopNavigation(storeId: $s) { __typename } }', {"s": 57}),
        ('{ navigation { __typename } }', None),
        ('{ categoryList { __typename } }', None),
    ]
    for q, v in attempts:
        body = gql(c, q, v)
        if "errors" not in body:
            print(f"  ✓ {q[:60]} -> {json.dumps(body)[:400]}")
        else:
            em = body["errors"][0]["message"]
            print(f"  ✗ {q[:60]} -> {em[:160]}")
        polite_sleep(0.3)

    # ============================================================
    # 2) Scrape homepage for /category/shop/<slug>/<parent>/<id> patterns
    # ============================================================
    print("\n" + "=" * 70)
    print("  Scrape homepage HTML for category URLs")
    print("=" * 70)
    r = c.get(HOMEPAGE)
    html = r.text
    # The breadcrumb format we saw: /category/shop/beverages/2864/490015
    # Also generic: /category/<path-segments>/<numericId>
    cat_urls = set(re.findall(r'/category/[a-z0-9/-]+/(\d+)/(\d+)["?]', html))
    cat_urls_with_slug = re.findall(r'/category/(shop/[a-z0-9-]+)/(\d+)/(\d+)', html)
    print(f"  Found {len(cat_urls)} unique (parentId, categoryId) pairs in homepage")
    for slug, parent, cid in cat_urls_with_slug[:40]:
        print(f"    {slug:50s} parent={parent} category={cid}")

    # Dedupe by categoryId
    unique_cids = {}
    for slug, parent, cid in cat_urls_with_slug:
        if cid not in unique_cids:
            unique_cids[cid] = {"slug": slug, "parent": parent}
    print(f"\n  Unique categoryIds: {len(unique_cids)}")

    # ============================================================
    # 3) Test each candidate via browseCategory
    # ============================================================
    print("\n" + "=" * 70)
    print("  Test which categories work via browseCategory")
    print("=" * 70)
    working = []
    not_working = []
    for cid, meta in list(unique_cids.items())[:60]:  # cap to 60
        result = browse_category(c, cid, limit=1)
        if "_errors" in result:
            em = result["_errors"][0].get("message", "")
            not_working.append((cid, meta, em))
        else:
            total = result.get("total", 0)
            records = result.get("records") or []
            working.append((cid, meta, total))
            sample_name = records[0].get("displayName", "")[:40] if records else ""
            print(f"  ✓ cid={cid:8s} parent={meta['parent']:8s} total={total:6d}  ({meta['slug']:30s}) sample={sample_name}")
        polite_sleep(0.3)

    print(f"\n  Working categories: {len(working)}")
    print(f"  Not-working categories: {len(not_working)}")

    # Save the working ones
    output = {
        "working_categories": [
            {"categoryId": cid, "slug": meta["slug"], "parentId": meta["parent"], "total": total}
            for cid, meta, total in working
        ],
        "not_working_categories": [
            {"categoryId": cid, "slug": meta["slug"], "parentId": meta["parent"], "error": em[:200]}
            for cid, meta, em in not_working
        ],
    }
    save_json(Path(__file__).parent.parent / "data" / "categories.json", output)
    print(f"\nSaved to data/categories.json")


if __name__ == "__main__":
    main()
