"""
Quick probe: find queryable categoryIds for H-E-B's top-level departments.

Strategy (revised):
1. Hardcode a candidate range based on what we've observed:
   - 490015 (Beverages, works)
   - 490024 (Pantry, from screenshot URL)
   - 490118, 490125 (Pantry children, from screenshot URLs)
   Try IDs in the 490000-490200 range as a brute force.
2. Also fetch a known department page and parse __NEXT_DATA__ to discover
   subcategory IDs the way we did for products.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import make_client, browse_category, save_json, polite_sleep, HOMEPAGE


def main():
    c = make_client()

    # ============================================================
    # Step 1: Fetch the Pantry category page and parse __NEXT_DATA__
    # ============================================================
    print("=" * 70)
    print("  Step 1: Fetch a known department page (Pantry) and parse __NEXT_DATA__")
    print("=" * 70)
    pantry_url = "https://www.heb.com/category/shop/pantry/2863/490024"
    r = c.get(pantry_url)
    print(f"  status: {r.status_code}, size: {len(r.text)}")
    discovered_ids = set()
    if r.status_code == 200:
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
        if m:
            try:
                nd = json.loads(m.group(1))
                # Walk for any 'categoryId' key with a 49xxxx value
                def walk(obj):
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k == "categoryId" and isinstance(v, (str, int)):
                                discovered_ids.add(str(v))
                            walk(v)
                    elif isinstance(obj, list):
                        for v in obj:
                            walk(v)
                walk(nd)
                # Also extract from URL strings in HTML (link hrefs)
                for m2 in re.finditer(r'/category/[a-z0-9/-]+/(\d+)/(\d+)', r.text):
                    discovered_ids.add(m2.group(1))
                    discovered_ids.add(m2.group(2))
                print(f"  IDs discovered in pantry page: {len(discovered_ids)}")
                print(f"    sample: {sorted(discovered_ids)[:30]}")
            except json.JSONDecodeError as e:
                print(f"  __NEXT_DATA__ parse error: {e}")

    # ============================================================
    # Step 2: Brute-force test category IDs in known range
    # ============================================================
    print("\n" + "=" * 70)
    print("  Step 2: Brute-force test 490000-490200 + IDs found in step 1")
    print("=" * 70)
    test_ids = set(str(i) for i in range(490000, 490200))
    test_ids |= discovered_ids
    # Filter to only numeric strings, drop tiny IDs like '0', '1', etc.
    test_ids = sorted(int(i) for i in test_ids if i.isdigit() and int(i) >= 1000)
    print(f"  Testing {len(test_ids)} candidate IDs...")

    working = []
    not_working_count = 0
    for cid in test_ids:
        result = browse_category(c, str(cid), limit=1)
        if "_errors" in result:
            not_working_count += 1
        else:
            total = result.get("total", 0)
            records = result.get("records") or []
            sample = records[0].get("displayName", "")[:40] if records else ""
            # Try to get name via breadcrumbs
            bcs = result.get("breadcrumbs") or []
            cat_name = ""
            for b in reversed(bcs):
                if b.get("title") and b.get("title") not in ("H-E-B", "Shop"):
                    cat_name = b["title"]
                    break
            working.append({
                "categoryId": str(cid),
                "name": cat_name,
                "total": total,
                "sample_product": sample,
                "breadcrumbs": [b.get("title") for b in bcs],
            })
            print(f"  ✓ {cid} total={total:6d}  name={cat_name:40s} sample={sample}")
        polite_sleep(0.25)

    print(f"\n  Working: {len(working)} / {len(test_ids)} tested")

    # Save
    save_json(Path(__file__).parent.parent / "data" / "categories.json", {
        "working_categories": working,
        "tested_count": len(test_ids),
        "not_working_count": not_working_count,
    })
    print(f"\nSaved to data/categories.json")

    # Print top categories by total
    if working:
        print("\n=== Top 20 categories by product count ===")
        for w in sorted(working, key=lambda x: -x["total"])[:20]:
            print(f"  {w['total']:6d}  cid={w['categoryId']:8s}  {w['name']}")


if __name__ == "__main__":
    main()
