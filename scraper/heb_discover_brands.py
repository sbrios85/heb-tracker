"""
H-E-B Brand Discovery
=====================
Walks H-E-B's selected top-level departments and collects all unique brand
names. Flags brands with isOwnBrand=true (H-E-B house brands).

Frozen food and Dairy & eggs are excluded (perishable/refrigerated, not
practical to ship via eBay from a home setup).

Output: data/brands.json
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import (
    make_client, browse_category, save_json, polite_sleep,
    load_blocked_brands,
    BRANDS_FILE, WALDRON_STORE_NUMBER,
)


# 10 top-level departments (Frozen + Dairy & eggs removed)
DEPARTMENTS = [
    ("490014", "Bakery & bread"),
    ("490015", "Beverages"),
    ("490017", "Deli & prepared food"),
    ("490018", "Everyday essentials"),
    ("490020", "Fruit & vegetables"),
    ("490021", "Health & beauty"),
    ("490022", "Home & outdoor"),
    ("490023", "Meat & seafood"),
    ("490024", "Pantry"),
    ("490025", "Pets"),
]

PAGE_SIZE = 60
MAX_PAGES_PER_DEPT = 170   # API caps at ~10,000 (167 pages × 60)


def normalize_brand_name(name: str) -> str:
    """Return the canonical form of a brand name for dedupe.
    We compare casefold + hyphen-stripped versions to detect HEB == H-E-B,
    OUR GOODS == our goods, etc. The FIRST capitalization we see wins as
    the canonical display form (alphabetical first form, generally).
    """
    return name.casefold().replace("-", "").replace(" ", "")


def main():
    print(f"H-E-B brand discovery — store {WALDRON_STORE_NUMBER}")
    print(f"Departments to walk: {len(DEPARTMENTS)}\n")

    client = make_client()

    # canonical_key -> brand dict (the first display name we encountered wins,
    # but if a 'better' variant shows up we don't care — they all map to one entry)
    brands_by_canonical: dict[str, dict] = {}
    # alternate_display_names: track variants we collapsed
    alt_names: dict[str, set] = {}

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
                break

            for rec in records:
                total_records_seen += 1
                dept_records += 1
                b = rec.get("brand") or {}
                name = b.get("name")
                if not name:
                    continue

                canonical = normalize_brand_name(name)
                if canonical not in brands_by_canonical:
                    # First time seeing this brand (under any capitalization)
                    brands_by_canonical[canonical] = {
                        "name": name,                  # display form
                        "isOwnBrand": bool(b.get("isOwnBrand")),
                        "count_in_sample": 0,
                        "departments_seen_in": [],
                        "first_seen_product_id": rec.get("id"),
                        "first_seen_product_name": rec.get("displayName"),
                    }
                    alt_names[canonical] = set()
                    dept_new_brands += 1
                else:
                    # Track that we saw a variant capitalization
                    existing_display = brands_by_canonical[canonical]["name"]
                    if name != existing_display:
                        alt_names[canonical].add(name)

                brands_by_canonical[canonical]["count_in_sample"] += 1
                if dept_name not in brands_by_canonical[canonical]["departments_seen_in"]:
                    brands_by_canonical[canonical]["departments_seen_in"].append(dept_name)

            dept_pages += 1
            total_pages_pulled += 1

            if page % 10 == 0 or page < 3:
                print(f"  page {page+1:3d} offset={offset:5d} got {len(records):3d} "
                      f"| brands total: {len(brands_by_canonical)} (+{dept_new_brands} new in dept) "
                      f"| dept total: {total}")

            offset += PAGE_SIZE
            if isinstance(total, int) and offset >= total:
                break
            polite_sleep()

        print(f"  {dept_name} done: {dept_pages} pages, {dept_records} records, "
              f"{dept_new_brands} new brands")

    # Attach alt_names to each entry
    for canonical, brand in brands_by_canonical.items():
        if alt_names[canonical]:
            brand["alternate_capitalizations"] = sorted(alt_names[canonical])

    print(f"\n--- Sampling complete ---")
    print(f"  pages pulled: {total_pages_pulled}")
    print(f"  product records seen: {total_records_seen}")
    print(f"  unique brands (after dedupe): {len(brands_by_canonical)}")

    # Filter out user-blocked brands
    blocked = load_blocked_brands()
    if blocked:
        print(f"\n  Blocked brands (from brand_blocklist.json): {len(blocked)}")
        for b_name in sorted(blocked):
            print(f"    - {b_name}")
        removed = [name for name in brands_by_canonical if brands_by_canonical[name]["name"] in blocked]
        for canonical in removed:
            del brands_by_canonical[canonical]
        print(f"  Removed {len(removed)} brand entr{'y' if len(removed)==1 else 'ies'} from output")

    own_brands = [b for b in brands_by_canonical.values() if b["isOwnBrand"]]
    print(f"  isOwnBrand=true count (after blocklist): {len(own_brands)}")

    # Sort: own brands first, then by count desc
    sorted_brands = sorted(
        brands_by_canonical.values(),
        key=lambda b: (not b["isOwnBrand"], -b["count_in_sample"]),
    )

    output = {
        "discovered_at": datetime.datetime.utcnow().isoformat() + "Z",
        "store_number": WALDRON_STORE_NUMBER,
        "departments_walked": [d[1] for d in DEPARTMENTS],
        "_sampling_stats": {
            "pages_pulled": total_pages_pulled,
            "product_records_seen": total_records_seen,
            "unique_brands_found": len(brands_by_canonical),
            "own_brands_found": len(own_brands),
            "blocked_brands_excluded": sorted(blocked),
        },
        "own_brands": [b for b in sorted_brands if b["isOwnBrand"]],
        "national_brands": [b for b in sorted_brands if not b["isOwnBrand"]],
    }
    save_json(BRANDS_FILE, output)
    print(f"\nSaved to {BRANDS_FILE}")

    print(f"\n=== H-E-B house brands discovered ({len(own_brands)}) ===")
    for b in sorted(own_brands, key=lambda x: -x["count_in_sample"]):
        depts = ", ".join(b["departments_seen_in"][:4])
        if len(b["departments_seen_in"]) > 4:
            depts += f" +{len(b['departments_seen_in']) - 4}"
        merge_note = ""
        if b.get("alternate_capitalizations"):
            merge_note = f"   [merged: {', '.join(b['alternate_capitalizations'])}]"
        print(f"  {b['count_in_sample']:5d}  {b['name']:45s}  [{depts}]{merge_note}")


if __name__ == "__main__":
    main()
