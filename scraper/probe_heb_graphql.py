"""
H-E-B GraphQL Probe — Phase 13
------------------------------
Phase 12 result: productDetail returns ZERO valid fields out of 118 candidates,
even with a real product ID. This isn't a naming problem — the schema gates
this query somehow.

Phase 13: stop guessing field names. Read H-E-B's actual JavaScript bundles
and find the literal query string their frontend sends for product detail.

Strategy:
1. Download all 50 JS chunks
2. Search every chunk for the literal text 'productDetail'
3. For each match, dump 3000 chars of surrounding context to find the
   compiled GraphQL query string
4. Also search for 'ProductDetailQuery', 'ProductDetailsPage', operation
   names with 'Product' in them
5. Print everything we find — the queries are in there
"""

import json
import re
import time
from pathlib import Path
import httpx

HOMEPAGE = "https://www.heb.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
}
OUTDIR = Path(__file__).parent.parent / "probe_output"
OUTDIR.mkdir(exist_ok=True)


def safe(s):
    return re.sub(r'[^A-Za-z0-9_\-.]', '_', str(s))[:80]


def save(name, data):
    p = OUTDIR / safe(name)
    p.write_text(json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data))


def section(t):
    print(f"\n{'=' * 70}\n  {t}\n{'=' * 70}")


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        section("Setup")
        r = c.get(HOMEPAGE)
        homepage_html = r.text
        print(f"  homepage: {r.status_code}, {len(homepage_html)} bytes")

        # Extract all JS chunk URLs
        js_urls = re.findall(r'<script[^>]+src="([^"]+\.js[^"]*)"', homepage_html)
        cx_urls = [u for u in js_urls if "cx.static.heb.com" in u]
        print(f"  found {len(cx_urls)} cx.static.heb.com chunks")

        # Patterns to find — order matters, more specific first
        SEARCH_PATTERNS = [
            r'productDetail',
            r'ProductDetailQuery',
            r'ProductDetailsPage',
            r'ProductDetail\w*',
        ]

        section("Searching each JS chunk for productDetail references")
        all_findings = {}
        for i, url in enumerate(cx_urls):
            try:
                r = c.get(url, timeout=30.0)
                if r.status_code != 200:
                    continue
                js = r.text
                short = url.split("/")[-1]
                chunk_findings = []

                # For each match of "productDetail" capture 3000 chars context
                for pat in SEARCH_PATTERNS:
                    for m in re.finditer(pat, js):
                        start = max(0, m.start() - 100)
                        end = min(len(js), m.end() + 3000)
                        context = js[start:end]
                        chunk_findings.append({
                            "pattern": pat,
                            "position": m.start(),
                            "context": context,
                        })
                        # Don't capture too many from one chunk
                        if len(chunk_findings) >= 5:
                            break
                    if len(chunk_findings) >= 5:
                        break

                if chunk_findings:
                    all_findings[short] = chunk_findings
                    print(f"  [{i:2d}] {short[:50]:50s} {len(chunk_findings)} matches")
            except Exception as e:
                print(f"  err {url}: {e}")
            time.sleep(0.2)

        save("phase13_all_findings.json", all_findings)

        # =====================================================
        # Print the most useful findings
        # =====================================================
        section("Top findings: chunks with most productDetail references")
        for chunk, findings in sorted(all_findings.items(), key=lambda x: -len(x[1]))[:8]:
            print(f"\n  ===== CHUNK: {chunk} ({len(findings)} findings) =====")
            for j, f in enumerate(findings[:3]):
                print(f"\n  --- finding {j+1} (pattern={f['pattern']}, pos={f['position']}) ---")
                print(f"  {f['context'][:2500]}")

        # =====================================================
        # Also harvest ALL GraphQL operation names from all chunks
        # =====================================================
        section("All GraphQL operation names harvested")
        all_ops = set()
        for chunk, findings in all_findings.items():
            for f in findings:
                # Find "query Foo(" or "mutation Foo("
                for m in re.finditer(r'(query|mutation)\s+([A-Z]\w+)', f['context']):
                    all_ops.add(m.group(2))
        # Also grab from raw chunks we haven't loaded yet by searching the homepage
        for op in sorted(all_ops):
            print(f"    {op}")
        save("phase13_operation_names.json", sorted(all_ops))

    print(f"\nDone.")


if __name__ == "__main__":
    main()
