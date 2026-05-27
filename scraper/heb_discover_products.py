"""
H-E-B Product Discovery
=======================
For every brand in data/brands.json, enumerate every product by walking
the brand's known departments with browseCategory + filter="brand:X".

This works because:
- productSearch requires a non-empty `query` (text search), so it can't be
  used to enumerate by brand alone — using a placeholder query like "a"
  returns 0 results because it actually searches for that literal text.
- browseCategory takes a categoryId (department) + optional filter, and
  the brand filter works on top of category enumeration.

We use the departments from brands.json's departments_seen_in field.

Output: data/products.json
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import (
    make_client, browse_category, save_json, load_json, polite_sleep,
    BRANDS_FILE, PRODUCTS_FILE, WALDRON_STORE_NUMBER,
)

# Map department display names -> categoryIds (from probe_categories.py)
DEPT_NAME_TO_ID = {
    "Bakery & bread": "490014",
    "Beverages": "490015",
    "Dairy & eggs": "490016",
    "Deli & prepared food": "490017",
    "Everyday essentials": "490018",
    "Frozen food": "490019",
    "Fruit & vegetables": "490020",
    "Health & beauty": "490021",
    "Home & outdoor": "490022",
    "Meat & seafood": "490023",
    "Pantry": "490024",
    "Pets": "490025",
}

PAGE_SIZE = 60
MAX_PAGES_PER_QUERY = 170  # API caps at 10,000


def enumerate_brand_in_department(client, brand_name, dept_name, dept_id, debug=False):
    """Walk one department for one brand. Returns list of product records."""
    products = []
    offset = 0
    seen_ids = set()
    for page in range(MAX_PAGES_PER_QUERY):
        result = browse_category(
            client, dept_id, brand=brand_name,
            limit=PAGE_SIZE, offset=offset,
        )
        if "_errors" in result:
            print(f"    ERROR: {result['_errors'][:1]}")
            break
        records = result.get("records") or []
        total = result.get("total")
        if page == 0 and debug:
            print(f"    [debug] first response: total={total}, "
                  f"first_record_brand={(records[0].get('brand') or {}).get('name') if records else None}")
        if not records:
            break

        for rec in records:
            pid = rec.get("id")
            if not pid or pid in seen_ids:
                continue
            # Defense: confirm the brand actually matches what we asked for
            rec_brand = (rec.get("brand") or {}).get("name", "")
            if rec_brand != brand_name:
                # The filter wasn't respected for this record — skip
                continue
            seen_ids.add(pid)
            products.append(rec)

        offset += PAGE_SIZE
        if isinstance(total, int) and offset >= total:
            break
        polite_sleep()
    return products, total


def main():
    print(f"H-E-B product discovery — store {WALDRON_STORE_NUMBER}\n")

    brands_data = load_json(BRANDS_FILE)
    if not brands_data:
        print(f"ERROR: {BRANDS_FILE} not found. Run heb_discover_brands.py first.")
        return

    own_brands = brands_data.get("own_brands") or []
    print(f"Brands to enumerate: {len(own_brands)}\n")
    if not own_brands:
        return

    client = make_client()
    today = datetime.date.today().isoformat()

    existing = load_json(PRODUCTS_FILE, default={"products": []})
    existing_by_id = {p["id"]: p for p in existing.get("products", [])}

    brand_results: list[dict] = []
    all_products: dict[str, dict] = {}

    for i, brand in enumerate(own_brands):
        bname = brand["name"]
        depts = brand.get("departments_seen_in") or []
        if not depts:
            print(f"[{i+1:2d}/{len(own_brands)}] {bname}: no departments listed, skipping")
            continue

        print(f"[{i+1:2d}/{len(own_brands)}] {bname}  ({len(depts)} dept(s))")

        brand_products = {}
        for dept_name in depts:
            dept_id = DEPT_NAME_TO_ID.get(dept_name)
            if not dept_id:
                # Department not in our walking set (e.g., Frozen/Dairy excluded)
                continue
            # Debug only on the very first brand's first dept
            debug = (i == 0 and dept_name == depts[0])
            recs, total = enumerate_brand_in_department(
                client, bname, dept_name, dept_id, debug=debug
            )
            for rec in recs:
                pid = rec.get("id")
                if pid in brand_products:
                    continue
                bcrumbs = rec.get("breadcrumbs") or []
                # Department is breadcrumbs[2] (after "H-E-B" and "Shop")
                rec_dept = ""
                if len(bcrumbs) >= 3:
                    rec_dept = bcrumbs[2].get("title", "")
                brand_products[pid] = {
                    "id": pid,
                    "displayName": rec.get("displayName"),
                    "brandName": (rec.get("brand") or {}).get("name") or bname,
                    "isOwnBrand": (rec.get("brand") or {}).get("isOwnBrand"),
                    "department": rec_dept or dept_name,
                    "first_seen": existing_by_id.get(pid, {}).get("first_seen", today),
                    "last_seen": today,
                    "inventory_state": (rec.get("inventory") or {}).get("inventoryState"),
                    "in_assortment": rec.get("inAssortment"),
                }
            print(f"    in {dept_name}: {len(recs)} new products (total reported: {total})")

        print(f"    {bname} TOTAL: {len(brand_products)} unique products")
        all_products.update(brand_products)
        brand_results.append({
            "brand": bname,
            "products_found": len(brand_products),
            "departments_searched": [d for d in depts if d in DEPT_NAME_TO_ID],
        })

    output = {
        "discovered_at": datetime.datetime.utcnow().isoformat() + "Z",
        "store_number": WALDRON_STORE_NUMBER,
        "stats": {
            "brands_enumerated": len(own_brands),
            "products_found": len(all_products),
            "per_brand": brand_results,
        },
        "products": sorted(
            all_products.values(),
            key=lambda p: (p["brandName"], p["displayName"] or ""),
        ),
    }
    save_json(PRODUCTS_FILE, output)

    print(f"\n=== Done ===")
    print(f"  unique products: {len(all_products)}")
    print(f"  saved to: {PRODUCTS_FILE}")
    print(f"\n=== Top 15 brands by product count ===")
    for r in sorted(brand_results, key=lambda x: -x["products_found"])[:15]:
        print(f"  {r['products_found']:5d}  {r['brand']}")


if __name__ == "__main__":
    main()
