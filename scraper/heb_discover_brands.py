"""
H-E-B Brand Discovery
=====================
Walks the H-E-B catalog under the "Shop" category root (categoryId=2864) and
collects all unique brand names. Filters to isOwnBrand=true.

Output: data/brands.json

  {
    "discovered_at": "2026-05-27T...",
    "store": 57,
    "store_name": "Flour Bluff H-E-B plus!",
    "brands": [
      {
        "name": "CAFE Olé by H-E-B",
        "isOwnBrand": true,
        "first_seen_product_id": "583162"
      },
      ...
    ],
    "_sampling_stats": { ... }
  }

Strategy:
  - browseCategory(categoryId: "2864") paginated returns the catalog.
  - We sample N pages (default: 200 pages × 60/page = 12,000 products)
    which is enough to cover most of the catalog without being abusive.
  - For each unique brand encountered, record name + isOwnBrand + first
    product ID we saw it on.
"""

import datetime
import sys
from pathlib import Path

# Path setup so we can import lib_heb
sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import (
    make_client, browse_category, save_json, polite_sleep,
    BRANDS_FILE, WALDRON_STORE_NUMBER,
)


# Multiple root-ish category IDs to maximize coverage. 2864 is "Shop" but
# different parts of the catalog may need different entry points.
ROOT_CATEGORIES = [
    ("2864", "Shop"),
    # Add more if first pass undersamples (e.g., dedicated grocery, beverages,
    # household, etc.). For now Shop is the umbrella.
]

# How many pages of 60 to pull from each category before stopping.
# 2864 should have thousands of products; we cap to be polite.
MAX_PAGES_PER_CATEGORY = 250
PAGE_SIZE = 60


def main():
    print(f"H-E-B brand discovery — store {WALDRON_STORE_NUMBER}")
    print(f"Sampling up to {MAX_PAGES_PER_CATEGORY * PAGE_SIZE} products per root category\n")

    client = make_client()

    # brand_name -> {isOwnBrand, first_seen_product_id, count}
    brands: dict[str, dict] = {}
    total_records_seen = 0
    pages_pulled = 0

    for cid, cname in ROOT_CATEGORIES:
        print(f"\n=== Root category: {cname} (id={cid}) ===")
        offset = 0
        for page in range(MAX_PAGES_PER_CATEGORY):
            result = browse_category(client, cid, limit=PAGE_SIZE, offset=offset)
            records = result.get("records") or []
            total = result.get("total", "?")
            if "_errors" in result:
                print(f"  page {page+1} offset={offset} ERRORS: {result['_errors']}")
                break
            if not records:
                print(f"  page {page+1} offset={offset} no records — stopping")
                break

            new_brand_count = 0
            for rec in records:
                total_records_seen += 1
                b = rec.get("brand") or {}
                name = b.get("name")
                if not name:
                    continue
                if name not in brands:
                    brands[name] = {
                        "name": name,
                        "isOwnBrand": bool(b.get("isOwnBrand")),
                        "first_seen_product_id": rec.get("id"),
                        "first_seen_product_name": rec.get("displayName"),
                        "count_in_sample": 0,
                    }
                    new_brand_count += 1
                brands[name]["count_in_sample"] += 1

            pages_pulled += 1
            print(f"  page {page+1:3d} offset={offset:5d} got {len(records):3d} records "
                  f"| brands seen so far: {len(brands)} (+{new_brand_count} new) "
                  f"| catalog total: {total}")

            offset += PAGE_SIZE
            # If we've covered the catalog, stop
            if total != "?" and offset >= total:
                print(f"  reached end of catalog (offset >= {total})")
                break
            polite_sleep()

    print(f"\n--- Sampling complete ---")
    print(f"  pages pulled: {pages_pulled}")
    print(f"  product records seen: {total_records_seen}")
    print(f"  unique brands found: {len(brands)}")
    own_brands = [b for b in brands.values() if b["isOwnBrand"]]
    print(f"  isOwnBrand=true count: {len(own_brands)}")

    # Sort brands: own brands first (by count desc), then everything else
    sorted_brands = sorted(
        brands.values(),
        key=lambda b: (not b["isOwnBrand"], -b["count_in_sample"]),
    )

    # Save
    output = {
        "discovered_at": datetime.datetime.utcnow().isoformat() + "Z",
        "store_number": WALDRON_STORE_NUMBER,
        "_sampling_stats": {
            "pages_pulled": pages_pulled,
            "product_records_seen": total_records_seen,
            "max_pages_per_category": MAX_PAGES_PER_CATEGORY,
            "page_size": PAGE_SIZE,
        },
        "own_brands": [b for b in sorted_brands if b["isOwnBrand"]],
        "national_brands": [b for b in sorted_brands if not b["isOwnBrand"]],
    }
    save_json(BRANDS_FILE, output)
    print(f"\nSaved to {BRANDS_FILE}")

    # Print the H-E-B house brands prominently
    print(f"\n=== H-E-B house brands discovered ({len(own_brands)}) ===")
    for b in sorted(own_brands, key=lambda x: -x["count_in_sample"]):
        print(f"  {b['count_in_sample']:5d}  {b['name']}")


if __name__ == "__main__":
    main()
