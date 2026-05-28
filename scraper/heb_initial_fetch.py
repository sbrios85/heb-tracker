"""
H-E-B Initial Bulk Fetch (GraphQL, store-correct)
=================================================
One-time bulk fetch of full product data for every product in
data/products.json, using getProductById(id, storeId: 57) — the GraphQL
path that returns correct Waldron (#57) pricing/availability/aisle.

Resumable: if interrupted, the next run skips products already in
details.json and continues.

Output: data/details.json
  {
    "last_updated": "...",
    "store_number": 57,
    "products": {
      "583162": { ...extract_product_summary fields..., "last_fetched": "..." },
      ...
    }
  }
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import (
    make_client, get_product_by_id, extract_product_summary,
    load_json, save_json, polite_sleep,
    PRODUCTS_FILE, DATA_DIR, WALDRON_STORE_NUMBER,
)

DETAILS_FILE = DATA_DIR / "details.json"
PROGRESS_SAVE_EVERY = 50
FETCH_DELAY = 0.5   # GraphQL is lighter than full HTML; ~2 req/s


def main():
    print(f"H-E-B initial bulk fetch (GraphQL, store #{WALDRON_STORE_NUMBER})")
    products_data = load_json(PRODUCTS_FILE)
    if not products_data:
        print(f"ERROR: {PRODUCTS_FILE} not found")
        return

    products = products_data.get("products") or []
    print(f"Total products in catalog: {len(products)}")

    details = load_json(DETAILS_FILE, default={"products": {}})
    if "products" not in details:
        details["products"] = {}
    already = set(details["products"].keys())
    print(f"Already fetched: {len(already)}")

    to_fetch = [p for p in products if p["id"] not in already]
    print(f"To fetch this run: {len(to_fetch)}")
    if not to_fetch:
        print("Nothing to do.")
        return

    client = make_client()
    fetched = failed = 0
    started = datetime.datetime.utcnow()
    store_check_done = False

    for i, product in enumerate(to_fetch):
        pid = product["id"]
        raw = get_product_by_id(client, pid, store_id=WALDRON_STORE_NUMBER)

        # Sanity check on first success: confirm aisle/price look store-specific
        if not store_check_done and raw is not None:
            store_check_done = True
            loc = (raw.get("productLocation") or {}).get("location")
            print(f"\n  *** FIRST FETCH OK: {raw.get('fullDisplayName','')[:50]} "
                  f"| aisle={loc} (store #{WALDRON_STORE_NUMBER}) ***\n")

        if raw is None:
            failed += 1
            details["products"][pid] = {
                "id": pid,
                "_failed": True,
                "last_attempted": datetime.datetime.utcnow().isoformat() + "Z",
            }
        else:
            summary = extract_product_summary(raw)
            summary["last_fetched"] = datetime.datetime.utcnow().isoformat() + "Z"
            # Carry through department + brand from the catalog entry
            summary["department"] = product.get("department")
            details["products"][pid] = summary
            fetched += 1

        if (i + 1) % 10 == 0 or (i + 1) == len(to_fetch):
            elapsed = (datetime.datetime.utcnow() - started).total_seconds()
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (len(to_fetch) - i - 1) / rate if rate > 0 else 0
            print(f"  [{i+1:5d}/{len(to_fetch)}] fetched={fetched} failed={failed} "
                  f"| {rate:.2f}/s | ETA {eta/60:.1f}min "
                  f"| {product.get('displayName','')[:45]}")

        if (i + 1) % PROGRESS_SAVE_EVERY == 0:
            details["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
            details["store_number"] = WALDRON_STORE_NUMBER
            save_json(DETAILS_FILE, details)

        polite_sleep(FETCH_DELAY)

    details["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
    details["store_number"] = WALDRON_STORE_NUMBER
    save_json(DETAILS_FILE, details)

    print(f"\n=== Done ===")
    print(f"  fetched this run: {fetched}")
    print(f"  failed this run:  {failed}")
    print(f"  total in details: {len(details['products'])}")
    print(f"  saved to: {DETAILS_FILE}")


if __name__ == "__main__":
    main()
