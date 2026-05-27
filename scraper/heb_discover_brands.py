"""
H-E-B Brand Discovery
=====================
Walks H-E-B's 12 top-level departments and collects all unique brand names.
Flags brands with isOwnBrand=true (H-E-B house brands).

Departments discovered via probe_categories.py — categoryIds 490014-490025:
  Bakery & bread, Beverages, Dairy & eggs, Deli & prepared food,
  Everyday essentials, Frozen food, Fruit & vegetables, Health & beauty,
  Home & outdoor, Meat & seafood, Pantry, Pets

H-E-B's browseCategory caps results at 10,000 per category, but we don't
need a complete product enumeration here — we just need every brand to
appear at least once. The first 10,000 products in any large category
will surface essentially all brand names sold there.

Output: data/brands.json
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import (
    make_client, browse_category, save_json, polite_sleep,
    BRANDS_FILE, WALDRON_STORE_NUMBER,
)


# The 12 top-level H-E-B departments
DEPARTMENTS = [
    ("490014", "Bakery & bread"),
    ("490015", "Beverages"),
    ("490016", "Dairy & eggs"),
    ("490017", "Deli & prepared food"),
    ("490018", "Everyday essentials"),
    ("490019", "Frozen food"),
    ("490020", "Fruit & vegetables"),
    ("490021", "Health & beauty"),
    ("490022", "Home & outdoor"),
    ("490023", "Meat & seafood"),
    ("490024", "Pantry"),
    ("490025", "Pets"),
]

PAGE_SIZE = 60
# Max pages per department. 167 pages × 60 = 10,020 (the API's hard cap).
# We set this slightly higher and trust the empty-result check to stop us.
MAX_PAGES_PER_DEPT = 170


def main():
    print(f"H-E-B brand discovery — store {WALDRON_STORE_NUMBER}")
    print(f"Departments to walk: {len(DEPARTMENTS)}\n")

    client = make_client()

    # brand_name -> { isOwnBrand, count, departments_seen_in, first_product }
    brands: dict[str, dict] = {}
    total_records_seen = 0
    total_pages_pulled = 0

    for cid, dept_name in DEPARTMENTS:
        print(f"\n=== {dept_name} (id={cid}) ===")
        dept_pages = 0
        dept_records = 0
        dept_new_brands = 0
        offset = 0

        for page in range(MAX_PAGES_PER_DEPT):
            result = browse_category(client, cid, limit=PAGE_SIZE, offset=offset)
            records = result.get("records") or []
            total = result.get("total", "?")

            if "_errors" in result:
                print(f"  page {page+1} ERRORS: {result['_errors']}")
                break
            if not records:
                # Reached end (real or capped)
                break

            for rec in records:
                total_records_seen += 1
                dept_records += 1
                b = rec.get("brand") or {}
                name = b.get("name")
                if not name:
                    continue
                if name not in brands:
                    brands[name] = {
                        "name": name,
                        "isOwnBrand": bool(b.get("isOwnBrand")),
                        "count_in_sample": 0,
                        "departments_seen_in": [],
                        "first_seen_product_id": rec.get("id"),
                        "first_seen_product_name": rec.get("displayName"),
                    }
                    dept_new_brands += 1
                brands[name]["count_in_sample"] += 1
                if dept_name not in brands[name]["departments_seen_in"]:
                    brands[name]["departments_seen_in"].append(dept_name)

            dept_pages += 1
            total_pages_pulled += 1

            if page % 10 == 0 or page < 3:
                print(f"  page {page+1:3d} offset={offset:5d} got {len(records):3d} "
                      f"| brands total: {len(brands)} (+{dept_new_brands} new in dept) "
                      f"| dept total: {total}")

            offset += PAGE_SIZE
            if isinstance(total, int) and offset >= total:
                break
            polite_sleep()

        print(f"  {dept_name} done: {dept_pages} pages, {dept_records} records, "
              f"{dept_new_brands} new brands")

    print(f"\n--- Sampling complete ---")
    print(f"  pages pulled: {total_pages_pulled}")
    print(f"  product records seen: {total_records_seen}")
    print(f"  unique brands found: {len(brands)}")
    own_brands = [b for b in brands.values() if b["isOwnBrand"]]
    print(f"  isOwnBrand=true count: {len(own_brands)}")

    # Sort brands by count desc within isOwnBrand groups
    sorted_brands = sorted(
        brands.values(),
        key=lambda b: (not b["isOwnBrand"], -b["count_in_sample"]),
    )

    output = {
        "discovered_at": datetime.datetime.utcnow().isoformat() + "Z",
        "store_number": WALDRON_STORE_NUMBER,
        "_sampling_stats": {
            "departments_walked": len(DEPARTMENTS),
            "pages_pulled": total_pages_pulled,
            "product_records_seen": total_records_seen,
            "unique_brands_found": len(brands),
            "own_brands_found": len(own_brands),
            "max_pages_per_dept": MAX_PAGES_PER_DEPT,
            "page_size": PAGE_SIZE,
        },
        "own_brands": [b for b in sorted_brands if b["isOwnBrand"]],
        "national_brands": [b for b in sorted_brands if not b["isOwnBrand"]],
    }
    save_json(BRANDS_FILE, output)
    print(f"\nSaved to {BRANDS_FILE}")

    # Print H-E-B house brands prominently
    print(f"\n=== H-E-B house brands discovered ({len(own_brands)}) ===")
    for b in sorted(own_brands, key=lambda x: -x["count_in_sample"]):
        depts = ", ".join(b["departments_seen_in"][:4])
        if len(b["departments_seen_in"]) > 4:
            depts += f" +{len(b['departments_seen_in']) - 4}"
        print(f"  {b['count_in_sample']:5d}  {b['name']:45s}  [{depts}]")


if __name__ == "__main__":
    main()
