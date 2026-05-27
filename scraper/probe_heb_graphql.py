"""
H-E-B GraphQL Probe — Phase 11
------------------------------
Phase 10 confirmed:
  - Product.id, displayName, inAssortment ✓
  - Product.brand{name}, inventory{inventoryState,quantity} ✓
  - Product.breadcrumbs[]{categoryId, title} ✓
  - filter "brand:X" works ONLY when paired with non-empty query or category
  - Still missing: price, image, URL/slug, pack size

Phase 11 goals:
  A) Probe ~120 NEW field name guesses for price/image/url/size
     (Apollo-style naming, from JS snippets, plural variants, etc.)
  B) Try browseCategory + brand filter (the working alternative to query="")
  C) Try productSearch with `category` arg instead of `query`
  D) Pull a full product with all confirmed fields
  E) Try a brand-name-only enumeration: pair with category 2864 (Shop root)
"""

import json
import re
import time
from pathlib import Path
import httpx

ENDPOINT = "https://www.heb.com/graphql"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.heb.com", "Referer": "https://www.heb.com/",
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


def post(c, q, v=None):
    payload = {"query": q}
    if v is not None:
        payload["variables"] = v
    return c.post(ENDPOINT, json=payload)


def probe_fields_fixed(client, template, candidates, label):
    """Field is valid if no errors, regardless of value."""
    valid_scalar, valid_complex, unknown, other = [], [], [], []
    for f in candidates:
        q = template.replace("__FIELD__", f)
        try:
            r = post(client, q)
            body = r.json() if r.text.startswith("{") else None
        except Exception:
            continue
        if not body:
            continue
        if "errors" in body and body["errors"]:
            em = body["errors"][0]["message"]
            if "Cannot query field" in em:
                unknown.append(f)
            elif "must have a selection of subfields" in em:
                m = re.search(r"of type [\"']([\w!\[\]]+)[\"']", em)
                inner = m.group(1) if m else "?"
                valid_complex.append({"field": f, "type": inner})
                print(f"  ⊞ {f:30s} NEEDS_SUBFIELDS type={inner}")
            else:
                other.append({"field": f, "err": em[:200]})
                print(f"  ? {f:30s} {em[:200]}")
        else:
            valid_scalar.append(f)
            data = body.get("data")
            # Drill in for display
            try:
                cur = data
                for k in ["productSearch", "records"]:
                    cur = cur.get(k) if isinstance(cur, dict) else (cur[0] if isinstance(cur, list) and cur else None)
                    if cur is None:
                        break
                if isinstance(cur, list) and cur:
                    cur = cur[0]
                v = cur.get(f) if isinstance(cur, dict) else None
                print(f"  ✓ {f:30s} -> {json.dumps(v)[:140] if v is not None else 'null'}")
            except Exception:
                print(f"  ✓ {f:30s} valid (could not extract value)")
        time.sleep(0.08)
    print(f"\n  [{label}] scalar={len(valid_scalar)} complex={len(valid_complex)} unknown={len(unknown)}")
    return {"scalar": valid_scalar, "complex": valid_complex, "unknown": unknown, "other": other}


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        # ====================================================
        # A) Heavy field probing — focused on price, image, url, size
        # ====================================================
        section("A) Probe MANY new field name candidates")
        template = '''query Q {
          productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", filter: "brand:CAFE Olé by H-E-B", limit: 1) {
            records { __FIELD__ }
          }
        }'''
        candidates = [
            # IDs and codes
            "primaryProductId", "productNumber", "primaryId",
            # Names
            "name", "title", "fullName", "shortName", "longName",
            # Pricing — exhaustive
            "price", "priceInfo", "priceDetails", "priceDisplay",
            "pricing", "prices", "pricesList",
            "regularPrice", "salePrice", "currentPrice", "listPrice",
            "displayPrice", "displayedUnitPrice", "displayedPrice",
            "unitPrice", "perUnitPrice", "unitOfMeasurePrice",
            "loyaltyPrice", "memberPrice", "specialPrice", "promoPrice",
            "shelfPrice", "everydayPrice", "originalPrice",
            "currentSellingPrice", "displayPriceInfo",
            "productPricing", "productPrice", "currentPriceDetails",
            "promotionalPrice", "isOnSale", "onSale",
            # Images — exhaustive
            "image", "imageInfo", "primaryImage", "primaryImageUrl",
            "imageUrl", "imageUri", "imagePath", "imageSrc",
            "images", "imageGallery", "imageGroup", "media",
            "thumbnail", "thumbnailUrl", "thumb",
            "smallImage", "mediumImage", "largeImage",
            "productImage", "productImages", "productMedia",
            "imageMetaData", "imageMetadata",
            "imageReference", "imageReferences",
            # URLs / slugs
            "url", "uri", "href", "link", "linkUrl",
            "slug", "productSlug", "urlSlug",
            "path", "permalink", "canonicalUrl",
            "productUrl", "detailUrl", "pdpUrl",
            "webUrl",
            # Size and packaging
            "size", "sizeText", "packageSize", "containerSize",
            "unitSize", "displaySize", "displayedSize",
            "uom", "unitOfMeasure", "uomLabel", "uomDisplay",
            "weight", "weightInfo", "netWeight",
            "packageInfo", "packaging", "packageType",
            "productSize", "productWeight", "productUom",
            # Misc
            "description", "shortDescription", "longDescription",
            "details", "productDetails", "productInformation",
            "info", "summary", "highlights",
            "aisle", "aisleLocation", "aisleNumber",
            "isOwnedBrand", "isPrivateLabel", "isHebOwnedBrand",
            "fulfillmentChannels", "channels",
            "tags", "labels", "badges", "flags",
            "snap", "snapEligible", "isSnapEligible", "ebt",
            "discontinued", "isDiscontinued",
            "active", "isActive",
            "ratings", "reviews", "averageRating", "ratingsAverage",
            "ingredients", "nutrition", "nutritionFacts",
            "warnings", "allergens", "dietary",
            # Numbers worth a shot
            "minSellQuantity", "maxSellQuantity",
            "isAgeRestricted", "ageRestriction",
        ]
        result = probe_fields_fixed(c, template, candidates, "Product-heavy")
        save("A_heavy_probe.json", result)

        # ====================================================
        # B) Probe browseCategory.records (same Product type)
        # ====================================================
        section("B) Try browseCategory + brand filter (no query needed?)")
        # browseCategory might accept filter too
        for cid in ["2864", "490015", "490036"]:
            for filt in ['"brand:CAFE Olé by H-E-B"', "null"]:
                q = f'''query Q {{
                  browseCategory(storeId: 57, shoppingContext: CURBSIDE_PICKUP, categoryId: "{cid}", filter: {filt}, limit: 1) {{
                    total
                    records {{ id displayName brand {{ name }} }}
                  }}
                }}'''
                r = post(c, q)
                try:
                    body = r.json()
                    if "errors" in body:
                        em = body["errors"][0]["message"]
                        print(f"  cid={cid} filter={filt[:30]:30s} ERR: {em[:160]}")
                    else:
                        ps = (body.get("data") or {}).get("browseCategory", {})
                        total = ps.get("total")
                        recs = ps.get("records") or []
                        sample = recs[0].get("displayName", "")[:50] if recs else ""
                        print(f"  cid={cid} filter={filt[:30]:30s} total={total} sample={sample}")
                        save(f"B_cid{cid}_filt{safe(filt)}.json", body)
                except Exception as e:
                    print(f"  err: {e}")
                time.sleep(0.3)

        # ====================================================
        # C) productSearch with `category` arg
        # ====================================================
        section("C) productSearch alternative args")
        attempts = [
            ('with category arg', 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, category: "2864", filter: "brand:CAFE Olé by H-E-B", limit: 1) { total records { displayName } } }'),
            ('with categoryId arg', 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, categoryId: "2864", filter: "brand:CAFE Olé by H-E-B", limit: 1) { total records { displayName } } }'),
            ('with searchText arg', 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, searchText: "coffee", filter: "brand:CAFE Olé by H-E-B", limit: 1) { total records { displayName } } }'),
            ('only filter', 'query Q { productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, filter: "brand:CAFE Olé by H-E-B", limit: 1) { total records { displayName } } }'),
        ]
        for tag, q in attempts:
            r = post(c, q)
            print(f"  {tag:25s} -> {r.text[:300]}")
            save(f"C_{safe(tag)}.json", r.text)
            time.sleep(0.3)

        # ====================================================
        # D) Pull a full product with confirmed + newly-found fields
        # ====================================================
        section("D) Final dump with everything we know works")
        all_scalars = ["id", "displayName", "inAssortment"] + [
            x for x in result["scalar"] if x not in ("id", "displayName", "inAssortment")
        ]
        complex_part = []
        for cx in result["complex"]:
            f, t = cx["field"], cx["type"]
            # Try __typename for unknown subfields
            complex_part.append(f"{f} {{ __typename }}")
        # Always include known-good complex expansions
        complex_part.extend([
            'brand { name }',
            'inventory { inventoryState quantity }',
            'breadcrumbs { categoryId title }',
        ])
        scalar_part = "\n              ".join(all_scalars)
        complex_part_str = "\n              ".join(complex_part)
        qfinal = f'''query Q {{
          productSearch(storeId: 57, shoppingContext: CURBSIDE_PICKUP, query: "coffee", filter: "brand:CAFE Olé by H-E-B", limit: 2) {{
            total
            records {{
              __typename
              {scalar_part}
              {complex_part_str}
            }}
          }}
        }}'''
        print("  Query:")
        print(qfinal)
        r = post(c, qfinal)
        print(f"\n  status: {r.status_code}")
        try:
            body = r.json()
            print(f"  body:\n{json.dumps(body, indent=2)[:7000]}")
        except Exception:
            print(f"  raw: {r.text[:4000]}")
        save("D_FINAL_DUMP.json", r.text)

    print(f"\nDone. Output in {OUTDIR}")


if __name__ == "__main__":
    main()
