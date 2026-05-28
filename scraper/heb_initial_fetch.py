"""
H-E-B Initial Bulk Fetch
========================
One-time bulk fetch of __NEXT_DATA__ for every product in data/products.json.
Pulls price, image, inventory, description, aisle, coupons.

Designed to be RESUMABLE — if a run hits the 6-hour GitHub Actions limit
or any other interruption, the next run picks up where it left off by
skipping products that already have data in details.json.

Output: data/details.json
  {
    "last_updated": "2026-05-27T...",
    "products": {
      "583162": {
        "id": "583162",
        "displayName": "...",
        "brandName": "...",
        "onlinePrice": 8.98,
        "imageUrl": "...",
        ...full extract_product_summary fields...
        "last_fetched": "2026-05-27T07:42:11Z"
      },
      ...
    }
  }

Throttle: 0.6s between requests = ~1.5 req/sec = ~4 hours for 9,945 products.
Configurable via FETCH_DELAY.
"""

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import (
    make_client, fetch_product_page, extract_product_summary,
    load_json, save_json, polite_sleep,
    PRODUCTS_FILE, DATA_DIR,
)

DETAILS_FILE = DATA_DIR / "details.json"
PROGRESS_SAVE_EVERY = 50  # save details.json every N products
FETCH_DELAY = 0.6


def main():
    print("H-E-B initial bulk fetch")
    products_data = load_json(PRODUCTS_FILE)
    if not products_data:
        print(f"ERROR: {PRODUCTS_FILE} not found")
        return

    products = products_data.get("products") or []
    print(f"Total products in catalog: {len(products)}")

    # Load existing details to resume
    details = load_json(DETAILS_FILE, default={"products": {}})
    if "products" not in details:
        details["products"] = {}
    already_fetched = set(details["products"].keys())
    print(f"Already fetched: {len(already_fetched)}")

    to_fetch = [p for p in products if p["id"] not in already_fetched]
    print(f"To fetch this run: {len(to_fetch)}")
    if not to_fetch:
        print("Nothing to do — all products fetched.")
        return

    client = make_client()
    fetched = 0
    failed = 0
    started = datetime.datetime.utcnow()
    store_check_done = False

    for i, product in enumerate(to_fetch):
        pid = product["id"]
        # We don't store URLs in products.json; fetch_product_page uses
        # "x" as a placeholder slug and H-E-B redirects to the canonical URL.
        raw = fetch_product_page(client, pid)

        # On the first successful fetch, verify the store actually pinned
        if not store_check_done and raw is not None:
            store_check_done = True
            returned_store = raw.get("storeId")
            print(f"\n  *** STORE CHECK: first product returned storeId={returned_store} "
                  f"(expected 57 for Waldron). If this is 92, store binding failed and "
                  f"prices will be Victoria's. ***\n")

        if raw is None:
            failed += 1
            # Mark as failed with timestamp so we can retry differently later
            details["products"][pid] = {
                "id": pid,
                "_failed": True,
                "last_attempted": datetime.datetime.utcnow().isoformat() + "Z",
            }
        else:
            summary = extract_product_summary(raw)
            summary["last_fetched"] = datetime.datetime.utcnow().isoformat() + "Z"
            # Add the URL slug we ended up at, for future direct fetches
            if raw.get("productPageURL"):
                summary["productPageURL"] = raw["productPageURL"]
            details["products"][pid] = summary
            fetched += 1

        # Periodic progress logging
        if (i + 1) % 10 == 0 or (i + 1) == len(to_fetch):
            elapsed = (datetime.datetime.utcnow() - started).total_seconds()
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta_sec = (len(to_fetch) - i - 1) / rate if rate > 0 else 0
            print(f"  [{i+1:5d}/{len(to_fetch)}] fetched={fetched} failed={failed} "
                  f"| {rate:.2f}/s | ETA {eta_sec/60:.1f}min "
                  f"| last: {product.get('displayName','')[:50]}")

        # Periodic save so we don't lose progress on crash
        if (i + 1) % PROGRESS_SAVE_EVERY == 0:
            details["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
            save_json(DETAILS_FILE, details)

        polite_sleep(FETCH_DELAY)

    # Final save
    details["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
    save_json(DETAILS_FILE, details)

    print(f"\n=== Done ===")
    print(f"  fetched this run: {fetched}")
    print(f"  failed this run:  {failed}")
    print(f"  total in details: {len(details['products'])}")
    print(f"  saved to: {DETAILS_FILE}")


if __name__ == "__main__":
    main()
