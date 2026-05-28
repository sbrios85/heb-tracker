"""
Merge data/details_chunk_*.json files into a single data/details.json.

Used at the end of the parallel matrix workflow.
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import load_json, save_json, DATA_DIR, WALDRON_STORE_NUMBER

DETAILS_FILE = DATA_DIR / "details.json"


def main():
    chunk_files = sorted(DATA_DIR.glob("details_chunk_*.json"))
    print(f"Found {len(chunk_files)} chunk files")

    merged = {"products": {}}
    per_chunk_stats = []

    for f in chunk_files:
        data = load_json(f)
        if not data:
            print(f"  {f.name}: empty or missing")
            continue
        prods = data.get("products") or {}
        ok = sum(1 for d in prods.values() if not d.get("_failed"))
        failed = len(prods) - ok
        per_chunk_stats.append({"file": f.name, "ok": ok, "failed": failed})
        print(f"  {f.name}: ok={ok} failed={failed}")
        merged["products"].update(prods)

    merged["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
    merged["store_number"] = WALDRON_STORE_NUMBER
    merged["_merge_stats"] = per_chunk_stats

    save_json(DETAILS_FILE, merged)

    total_ok = sum(1 for d in merged["products"].values() if not d.get("_failed"))
    total_failed = len(merged["products"]) - total_ok
    print(f"\n=== Merged ===")
    print(f"  total products: {len(merged['products'])}")
    print(f"  ok:     {total_ok}")
    print(f"  failed: {total_failed}")
    print(f"  saved to {DETAILS_FILE}")


if __name__ == "__main__":
    main()
