"""
H-E-B Daily Refresh
===================
Runs daily. For every LISTED product (products with >=1 eBay listing in
tracked.json), fetches current data via getProductById(storeId:57) and
compares against the previous snapshot. Detects and logs:

  - price change (onlinePrice or onlineSalePrice)
  - sale status change (isOnSale flipped)
  - out of stock (inventoryState left IN_STOCK)
  - discontinued (product no longer returned / inAssortment false)
  - name change (fullDisplayName)
  - description change (productDescription)
  - image change (image bytes hash differs)

Writes:
  - data/prices/YYYY-MM-DD.json   today's full snapshot of listed products
  - data/alerts.json              running list of unresolved alerts

The FIRST run establishes a baseline: it writes today's snapshot but fires
NO alerts (nothing to compare against yet).

Only LISTED products are refreshed (per user's choice). Tracked-but-not-listed
products are ignored until they get an eBay listing.
"""

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import (
    make_client, get_product_by_id_resilient, extract_product_summary,
    hash_image_bytes, load_json, save_json, polite_sleep,
    DATA_DIR, WALDRON_STORE_NUMBER,
)

TRACKED_FILE = DATA_DIR / "tracked.json"
PRICES_DIR = DATA_DIR / "prices"
ALERTS_FILE = DATA_DIR / "alerts.json"

# Alert severity: critical alerts also trigger a GitHub Issue
CRITICAL_TYPES = {"out_of_stock", "discontinued"}


def listed_product_ids(tracked_data: dict) -> list:
    """Return the ids of products that have >=1 eBay listing."""
    listings = tracked_data.get("listings") or {}
    return [pid for pid, arr in listings.items() if isinstance(arr, list) and len(arr) > 0]


def most_recent_snapshot() -> dict:
    """Load the most recent daily snapshot (before today), or {} if none."""
    if not PRICES_DIR.exists():
        return {}
    today = datetime.date.today().isoformat()
    files = sorted(PRICES_DIR.glob("????-??-??.json"))
    # Exclude today's file if it exists (we're rewriting it)
    files = [f for f in files if f.stem != today]
    if not files:
        return {}
    return load_json(files[-1], default={})


def detect_changes(pid, prev, curr):
    """Compare a product's previous vs current snapshot; return list of alert dicts."""
    alerts = []
    now = datetime.datetime.utcnow().isoformat() + "Z"
    name = curr.get("displayName") or prev.get("displayName") or pid

    def mk(alert_type, message, old, new, severity):
        return {
            "id": pid,
            "productName": name,
            "type": alert_type,
            "message": message,
            "old": old,
            "new": new,
            "severity": severity,
            "detected_at": now,
            "resolved": False,
        }

    # Price change (compare the effective price: sale price if on sale else list)
    prev_price = prev.get("onlineSalePrice") if prev.get("isOnSale") else prev.get("onlinePrice")
    curr_price = curr.get("onlineSalePrice") if curr.get("isOnSale") else curr.get("onlinePrice")
    if prev_price is not None and curr_price is not None and prev_price != curr_price:
        direction = "up" if curr_price > prev_price else "down"
        alerts.append(mk(
            "price_change",
            f"Price {direction}: ${prev_price:.2f} → ${curr_price:.2f}",
            prev_price, curr_price, "info",
        ))

    # Sale status flip
    if bool(prev.get("isOnSale")) != bool(curr.get("isOnSale")):
        if curr.get("isOnSale"):
            alerts.append(mk("on_sale", "Now ON SALE", False, True, "info"))
        else:
            alerts.append(mk("off_sale", "No longer on sale", True, False, "info"))

    # Out of stock
    prev_stock = prev.get("inventoryState")
    curr_stock = curr.get("inventoryState")
    if prev_stock == "IN_STOCK" and curr_stock and curr_stock != "IN_STOCK":
        alerts.append(mk("out_of_stock", f"Out of stock ({curr_stock})", prev_stock, curr_stock, "critical"))
    elif prev_stock and prev_stock != "IN_STOCK" and curr_stock == "IN_STOCK":
        alerts.append(mk("back_in_stock", "Back in stock", prev_stock, curr_stock, "info"))

    # Name change
    if prev.get("displayName") and curr.get("displayName") and prev["displayName"] != curr["displayName"]:
        alerts.append(mk("name_change", "Product name changed (possible size/formula change)",
                         prev["displayName"], curr["displayName"], "warning"))

    # Description change
    if prev.get("productDescription") and curr.get("productDescription") \
            and prev["productDescription"] != curr["productDescription"]:
        alerts.append(mk("description_change", "Description changed (possible reformulation)",
                         prev.get("productDescription", "")[:100],
                         curr.get("productDescription", "")[:100], "warning"))

    # Size change (parsed size)
    if prev.get("size") and curr.get("size") and prev["size"] != curr["size"]:
        alerts.append(mk("size_change", f"Size changed: {prev['size']} → {curr['size']}",
                         prev["size"], curr["size"], "warning"))

    # Image change (hash differs)
    if prev.get("imageHash") and curr.get("imageHash") and prev["imageHash"] != curr["imageHash"]:
        alerts.append(mk("image_change", "Product image changed (packaging update)",
                         prev["imageHash"], curr["imageHash"], "warning"))

    return alerts


def main():
    print(f"H-E-B daily refresh (store #{WALDRON_STORE_NUMBER})")
    today = datetime.date.today().isoformat()

    tracked_data = load_json(TRACKED_FILE, default={})
    listed_ids = listed_product_ids(tracked_data)
    print(f"Listed products to refresh: {len(listed_ids)}")

    if not listed_ids:
        print("No listed products. Nothing to refresh. (Add eBay listings in the dashboard.)")
        # Still write an empty snapshot so the baseline exists
        save_json(PRICES_DIR / f"{today}.json", {})
        return

    prev_snapshot = most_recent_snapshot()
    is_first_run = len(prev_snapshot) == 0
    if is_first_run:
        print("No previous snapshot found — this run establishes the BASELINE (no alerts).")

    client = make_client()
    today_snapshot = {}
    all_new_alerts = []

    for i, pid in enumerate(listed_ids):
        raw, _ = get_product_by_id_resilient(client, pid, store_id=WALDRON_STORE_NUMBER)
        if raw is None:
            # Product no longer returned → discontinued
            print(f"  [{i+1}/{len(listed_ids)}] {pid}: NOT RETURNED (discontinued?)")
            summary = {"id": pid, "_notReturned": True, "last_checked": today}
            today_snapshot[pid] = summary
            if not is_first_run and pid in prev_snapshot and not prev_snapshot[pid].get("_notReturned"):
                all_new_alerts.append({
                    "id": pid,
                    "productName": prev_snapshot[pid].get("displayName", pid),
                    "type": "discontinued",
                    "message": "Product no longer returned by H-E-B (discontinued or delisted)",
                    "old": "available", "new": "gone",
                    "severity": "critical",
                    "detected_at": datetime.datetime.utcnow().isoformat() + "Z",
                    "resolved": False,
                })
            polite_sleep(0.5)
            continue

        summary = extract_product_summary(raw)
        summary["last_checked"] = today
        # Image hash (fetch the image bytes)
        summary["imageHash"] = hash_image_bytes(client, summary.get("imageUrl"))
        today_snapshot[pid] = summary

        # Compare against previous
        if not is_first_run and pid in prev_snapshot:
            changes = detect_changes(pid, prev_snapshot[pid], summary)
            if changes:
                for ch in changes:
                    print(f"  ⚠ {pid}: {ch['type']} — {ch['message']}")
                all_new_alerts.extend(changes)

        price = summary.get("onlineSalePrice") if summary.get("isOnSale") else summary.get("onlinePrice")
        print(f"  [{i+1}/{len(listed_ids)}] {summary.get('displayName','')[:45]} "
              f"| ${price} | {summary.get('inventoryState')} | size={summary.get('size') or '—'}")
        polite_sleep(0.5)

    # Save today's snapshot
    save_json(PRICES_DIR / f"{today}.json", today_snapshot)
    print(f"\nSnapshot saved: data/prices/{today}.json ({len(today_snapshot)} products)")

    # Merge new alerts into alerts.json (keep unresolved history)
    alerts_data = load_json(ALERTS_FILE, default={"alerts": []})
    existing = alerts_data.get("alerts", [])
    # Avoid duplicate alerts: same id+type+detected same day
    existing_keys = {(a["id"], a["type"], a["detected_at"][:10]) for a in existing}
    added = 0
    for a in all_new_alerts:
        key = (a["id"], a["type"], a["detected_at"][:10])
        if key not in existing_keys:
            existing.append(a)
            existing_keys.add(key)
            added += 1
    alerts_data["alerts"] = existing
    alerts_data["last_refresh"] = datetime.datetime.utcnow().isoformat() + "Z"
    save_json(ALERTS_FILE, alerts_data)

    n_critical = sum(1 for a in all_new_alerts if a["severity"] == "critical")
    print(f"\nNew alerts: {added} ({n_critical} critical)")
    if is_first_run:
        print("(Baseline run — alerts suppressed. Change detection starts tomorrow.)")

    # Write a flag file the workflow can read to decide whether to open a GitHub Issue
    critical_alerts = [a for a in all_new_alerts if a["severity"] == "critical"]
    save_json(DATA_DIR / "_critical_today.json", {
        "date": today,
        "count": len(critical_alerts),
        "alerts": critical_alerts,
    })


if __name__ == "__main__":
    main()
