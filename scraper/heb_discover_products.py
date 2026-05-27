"""
H-E-B Product Discovery
=======================
For every brand in data/brands.json (own_brands list), enumerate every
product via productSearch(filter: "brand:X"). Writes data/products.json.

The output is the canonical "tracked products" list. The daily refresh
script will pull current pricing/availability for each one.

Output: data/products.json
  {
    "discovered_at": "...",
    "store_number": 57,
    "stats": {...},
    "products": [
      {
        "id": "583162",
        "displayName": "...",
        "brandName": "CAFE Olé by H-E-B",
        "departments": ["Beverages"],
        "first_seen": "2026-05-27",
        "last_seen": "2026-05-27"
      },
      ...
    ]
  }
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import (
    make_client, product_search, save_json, load_json, polite_sleep,
    BRANDS_FILE, PRODUCTS_FILE, WALDRON_STORE_NUMBER,
)


PAGE_SIZE = 60
# H-E-B's API caps results at 10,000 even with filters, so 167 pages is the ceiling
MAX_PAGES_PER_BRAND = 170

# productSearch requires a non-empty query. Use a single character — H-E-B
# treats this loosely and returns all matching-brand products.
SEARCH_QUERY = "a"


def main():
    print(f"H-E-B product discovery — store {WALDRON_STORE_NUMBER}\n")

    brands_data = load_json(BRANDS_FILE)
    if not brands_data:
        print(f"ERROR: {BRANDS_FILE} not found or empty. Run heb_discover_brands.py first.")
        return

    own_brands = brands_data.get("own_brands") or []
    print(f"Brands to enumerate: {len(own_brands)}")
    if not own_brands:
        print("No own_brands in brands.json — nothing to do.")
        return

    client = make_client()

    today = datetime.date.today().isoformat()
    existing = load_json(PRODUCTS_FILE, default={"products": []})
    existing_by_id = {p["id"]: p for p in existing.get("products", [])}

    # Per-brand stats
    brand_results: list[dict] = []
    all_products: dict[str, dict] = {}

    for i, brand in enumerate(own_brands):
        bname = brand["name"]
        # Use the API filter syntax we proved: "brand:<exact name>"
        # Probe revealed that filter is silently ignored when malformed
        # (returns total=1170 or similar). We trust our discovered names.
        print(f"\n[{i+1:2d}/{len(own_brands)}] {bname}")
        offset = 0
        products_this_brand = 0
        api_total_reported = None

        for page in range(MAX_PAGES_PER_BRAND):
            result = product_search(
                client,
                query=SEARCH_QUERY,
                brand=bname,
                limit=PAGE_SIZE,
                offset=offset,
            )
            if "_errors" in result:
                print(f"  ERROR at offset {offset}: {result['_errors']}")
                break

            records = result.get("records") or []
            total = result.get("total")
            if api_total_reported is None:
                api_total_reported = total

            if not records:
                break

            for rec in records:
                pid = rec.get("id")
                if not pid:
                    continue
                # Filter out any rec whose brand doesn't actually match.
                # The filter is permissive — if it didn't match, total would
                # be ~1170 (everything). Defensive check anyway.
                rec_brand = (rec.get("brand") or {}).get("name", "")
                if rec_brand and rec_brand != bname:
                    # Some merged brands might be permissive; skip silently
                    continue

                bcrumbs = rec.get("breadcrumbs") or []
                dept = ""
                # Department is the second breadcrumb (after H-E-B root)
                # breadcrumbs[1] is "Shop", [2] is the department
                if len(bcrumbs) >= 3:
                    dept = bcrumbs[2].get("title", "")

                if pid in all_products:
                    # Already tracked this product under another brand variant
                    continue

                entry = {
                    "id": pid,
                    "displayName": rec.get("displayName"),
                    "brandName": rec_brand or bname,
                    "isOwnBrand": (rec.get("brand") or {}).get("isOwnBrand"),
                    "department": dept,
                    "first_seen": existing_by_id.get(pid, {}).get("first_seen", today),
                    "last_seen": today,
                    "inventory_state": (rec.get("inventory") or {}).get("inventoryState"),
                    "in_assortment": rec.get("inAssortment"),
                }
                all_products[pid] = entry
                products_this_brand += 1

            offset += PAGE_SIZE
            if isinstance(total, int) and offset >= total:
                break
            if page > 0 and page % 20 == 0:
                print(f"  ... offset={offset} products={products_this_brand}")
            polite_sleep()

        print(f"  {bname}: {products_this_brand} products "
              f"(api total: {api_total_reported})")
        brand_results.append({
            "brand": bname,
            "products_found": products_this_brand,
            "api_total_reported": api_total_reported,
        })

    output = {
        "discovered_at": datetime.datetime.utcnow().isoformat() + "Z",
        "store_number": WALDRON_STORE_NUMBER,
        "stats": {
            "brands_enumerated": len(own_brands),
            "products_found": len(all_products),
            "per_brand": brand_results,
        },
        "products": sorted(all_products.values(), key=lambda p: (p["brandName"], p["displayName"] or "")),
    }
    save_json(PRODUCTS_FILE, output)

    print(f"\n=== Done ===")
    print(f"  brands enumerated: {len(own_brands)}")
    print(f"  unique products: {len(all_products)}")
    print(f"  saved to: {PRODUCTS_FILE}")


if __name__ == "__main__":
    main()
