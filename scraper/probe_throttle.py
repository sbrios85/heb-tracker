"""
Probe: what resets H-E-B's getProductById throttle?

We deliberately trip the throttle (~1040 rapid getProductById calls), then
test three recovery strategies to see which clears it:
  A) fresh session (new client/cookies, same IP)
  B) waiting 60s on the same session
  C) waiting 60s THEN fresh session

This tells us whether to use session-cycling, cooldowns, or chunked runs.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import make_client, get_product_by_id, WALDRON_STORE_NUMBER, load_json, PRODUCTS_FILE

# Use real product IDs from the catalog so misses mean throttle, not bad IDs
def get_test_ids(n):
    data = load_json(PRODUCTS_FILE)
    prods = data.get("products") or []
    return [p["id"] for p in prods[:n]]


def burst(client, ids, label):
    """Fire requests until we hit a miss. Return (successes_before_miss, hit_throttle)."""
    ok = 0
    for pid in ids:
        r = get_product_by_id(client, pid, WALDRON_STORE_NUMBER)
        if r is None:
            return ok, True
        ok += 1
        time.sleep(0.3)
    return ok, False


def probe_n(client, ids, n=5):
    """Try n products, return how many succeed."""
    ok = 0
    for pid in ids[:n]:
        r = get_product_by_id(client, pid, WALDRON_STORE_NUMBER)
        if r is not None:
            ok += 1
        time.sleep(0.5)
    return ok


def main():
    ids = get_test_ids(1300)
    print(f"Loaded {len(ids)} real product IDs for testing\n")

    # ---- Step 1: trip the throttle ----
    print("=" * 70)
    print("  Step 1: Fire requests until throttled")
    print("=" * 70)
    c = make_client()
    ok = 0
    throttled_at = None
    for idx, pid in enumerate(ids):
        r = get_product_by_id(c, pid, WALDRON_STORE_NUMBER)
        if r is None:
            throttled_at = idx
            print(f"  THROTTLED at request #{idx} (after {ok} successes)")
            break
        ok += 1
        if (idx + 1) % 100 == 0:
            print(f"  ...{idx+1} requests, {ok} ok")
        time.sleep(0.3)
    if throttled_at is None:
        print(f"  Never throttled in {len(ids)} requests?! Cap may be higher now.")
        return

    # Confirm we're really throttled: try 3 more, expect misses
    print(f"\n  Confirming throttle (same session, immediate)...")
    confirm = probe_n(c, ids[throttled_at:], 3)
    print(f"    {confirm}/3 succeeded immediately after throttle (expect 0)")

    # ---- Approach A: fresh session, same IP ----
    print("\n" + "=" * 70)
    print("  A) Fresh session (new cookies, same IP) — no wait")
    print("=" * 70)
    c2 = make_client()
    a_ok = probe_n(c2, ids[throttled_at:], 5)
    print(f"  fresh session: {a_ok}/5 succeeded  {'✓ RESETS IT' if a_ok >= 3 else '✗ still throttled'}")

    # ---- Approach B: wait 60s on ORIGINAL throttled session ----
    print("\n" + "=" * 70)
    print("  B) Wait 60s on the SAME throttled session")
    print("=" * 70)
    print("  waiting 60s...")
    time.sleep(60)
    b_ok = probe_n(c, ids[throttled_at:], 5)
    print(f"  after 60s wait: {b_ok}/5 succeeded  {'✓ RESETS IT' if b_ok >= 3 else '✗ still throttled'}")

    # ---- Approach C: wait + fresh session ----
    print("\n" + "=" * 70)
    print("  C) Already waited 60s; now also fresh session")
    print("=" * 70)
    c3 = make_client()
    c_ok = probe_n(c3, ids[throttled_at:], 5)
    print(f"  wait+fresh: {c_ok}/5 succeeded  {'✓ RESETS IT' if c_ok >= 3 else '✗ still throttled'}")

    # ---- Verdict ----
    print("\n" + "=" * 70)
    print("  VERDICT")
    print("=" * 70)
    print(f"  throttle tripped at: ~{throttled_at} requests")
    print(f"  A) fresh session (no wait):  {a_ok}/5")
    print(f"  B) 60s wait (same session):  {b_ok}/5")
    print(f"  C) 60s wait + fresh session: {c_ok}/5")
    if a_ok >= 3:
        print("  => SESSION-CYCLING WORKS. Cycle client before ~1000 requests.")
    elif b_ok >= 3 or c_ok >= 3:
        print("  => NEEDS A WAIT. Add a cooldown when throttled.")
    else:
        print("  => HARD IP CAP. Use chunked runs spaced apart (resumable).")


if __name__ == "__main__":
    main()
