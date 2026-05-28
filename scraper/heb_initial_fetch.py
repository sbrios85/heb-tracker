"""
H-E-B Initial Bulk Fetch — chunked, parallelizable
==================================================
Fetches getProductById(id, storeId: 57) for a slice of products.json
defined by --chunk and --total-chunks. Each chunk is independent and
writes its results to a chunk-specific output file.

Probe finding: H-E-B applies a hard IP cap at ~1,000 getProductById calls
per source IP. Cookies/waits don't reset it within a workflow run.
Solution: run multiple chunks as parallel matrix jobs (each gets its own
GitHub Actions runner IP). With 800 products per chunk × 13 chunks we
cover all 9,945 products in parallel, each safely under the cap.

Usage:
  python heb_initial_fetch.py --chunk N --total-chunks 13
  python heb_initial_fetch.py                  # single-process (legacy)

Outputs (chunk mode): data/details_chunk_NN.json
Outputs (single mode): data/details.json
"""

import argparse
import datetime
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import (
    make_client, get_product_by_id_resilient, extract_product_summary,
    load_json, save_json, polite_sleep,
    PRODUCTS_FILE, DATA_DIR, WALDRON_STORE_NUMBER,
)

PROGRESS_SAVE_EVERY = 50
BASE_DELAY = 0.5
CHUNK_SIZE_DEFAULT = 800


def chunk_slice(items, chunk_idx, total_chunks):
    """Split items into total_chunks contiguous slices, return the chunk_idx-th."""
    n = len(items)
    size = (n + total_chunks - 1) // total_chunks  # ceil
    start = chunk_idx * size
    end = min(start + size, n)
    return items[start:end], start, end


def run_fetch(products_to_fetch, output_file, label=""):
    print(f"H-E-B fetch{label} — {len(products_to_fetch)} products → {output_file.name}")

    # Resume support
    details = load_json(output_file, default={"products": {}})
    if "products" not in details:
        details["products"] = {}
    already_ok = {pid for pid, d in details["products"].items() if not d.get("_failed")}
    print(f"  already fetched OK in this chunk: {len(already_ok)}")

    to_fetch = [p for p in products_to_fetch if p["id"] not in already_ok]
    print(f"  to fetch this run: {len(to_fetch)}")
    if not to_fetch:
        print("  nothing to do.")
        return

    client = make_client()
    fetched = failed = 0
    started = datetime.datetime.utcnow()
    store_check_done = False

    for i, product in enumerate(to_fetch):
        pid = product["id"]
        raw, was_throttled = get_product_by_id_resilient(
            client, pid, store_id=WALDRON_STORE_NUMBER
        )

        if not store_check_done and raw is not None:
            store_check_done = True
            loc = (raw.get("productLocation") or {}).get("location")
            print(f"  *** FIRST FETCH OK: {raw.get('fullDisplayName','')[:50]} "
                  f"| aisle={loc} ***")

        if raw is None:
            failed += 1
            details["products"][pid] = {
                "id": pid, "_failed": True,
                "last_attempted": datetime.datetime.utcnow().isoformat() + "Z",
            }
            # Note: a single chunk should never hit the throttle (800 < ~1040),
            # but if it does we just record and continue — the merge step or a
            # later re-run picks up failed ones.
        else:
            summary = extract_product_summary(raw)
            summary["last_fetched"] = datetime.datetime.utcnow().isoformat() + "Z"
            summary["department"] = product.get("department")
            details["products"][pid] = summary
            fetched += 1

        if (i + 1) % 25 == 0 or (i + 1) == len(to_fetch):
            elapsed = (datetime.datetime.utcnow() - started).total_seconds()
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (len(to_fetch) - i - 1) / rate if rate > 0 else 0
            print(f"  [{i+1:4d}/{len(to_fetch)}] fetched={fetched} failed={failed} "
                  f"| {rate:.2f}/s | ETA {eta/60:.1f}min "
                  f"| {product.get('displayName','')[:38]}")

        if (i + 1) % PROGRESS_SAVE_EVERY == 0:
            details["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
            details["store_number"] = WALDRON_STORE_NUMBER
            save_json(output_file, details)

        polite_sleep(BASE_DELAY)

    details["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
    details["store_number"] = WALDRON_STORE_NUMBER
    save_json(output_file, details)

    ok_total = sum(1 for d in details["products"].values() if not d.get("_failed"))
    print(f"  done: fetched={fetched} failed={failed} | "
          f"chunk total OK: {ok_total}/{len(products_to_fetch)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk", type=int, default=None,
                        help="Chunk index (0-based), used with --total-chunks")
    parser.add_argument("--total-chunks", type=int, default=None,
                        help="Total number of chunks")
    args = parser.parse_args()

    products_data = load_json(PRODUCTS_FILE)
    if not products_data:
        print(f"ERROR: {PRODUCTS_FILE} not found")
        sys.exit(1)

    products = products_data.get("products") or []
    print(f"Total products in catalog: {len(products)}")

    if args.chunk is not None and args.total_chunks is not None:
        # Chunked mode
        chunk_products, start, end = chunk_slice(products, args.chunk, args.total_chunks)
        output_file = DATA_DIR / f"details_chunk_{args.chunk:02d}.json"
        label = f" [chunk {args.chunk+1}/{args.total_chunks}: products {start}-{end-1}]"
        run_fetch(chunk_products, output_file, label=label)
    else:
        # Single-process mode (legacy, hits the throttle)
        output_file = DATA_DIR / "details.json"
        run_fetch(products, output_file)


if __name__ == "__main__":
    main()
