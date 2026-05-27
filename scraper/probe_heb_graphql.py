"""
H-E-B GraphQL Probe — Phase 12
------------------------------
Phase 11 revealed the architecture:
  - productSearch/browseCategory return STRIPPED-DOWN Product objects
    (id, displayName, brand, inventory, breadcrumbs) for listing pages.
  - For price/image/URL we MUST use productDetail.
  - Two-step pattern: list IDs from search, fetch details one-by-one.

Phase 12 strategy:
  - Re-probe productDetail with FIXED logic (a field is valid if no errors,
    regardless of value). Earlier "0 valid" result was the same probe bug
    as Phase 9 with the records[] path.
  - Use a real product ID (583162 — CAFE Olé Texas Pecan Ground Coffee).
  - If we get price/image/URL fields, we have everything.
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


def probe_fixed(client, template, vars_, candidates, label):
    """Field is valid if no errors. Drill into response to extract value."""
    valid_scalar, valid_complex, unknown, other = [], [], [], []
    for f in candidates:
        q = template.replace("__FIELD__", f)
        try:
            r = post(client, q, vars_)
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
                print(f"  ? {f:30s} {em[:160]}")
        else:
            valid_scalar.append(f)
            # Extract value
            data = body.get("data") or {}
            cur = data.get("productDetail") if "productDetail" in data else data
            if isinstance(cur, dict):
                v = cur.get(f)
                preview = json.dumps(v)[:140] if v is not None else "null"
                print(f"  ✓ {f:30s} -> {preview}")
            else:
                print(f"  ✓ {f:30s} valid")
        time.sleep(0.08)
    print(f"\n  [{label}] scalar={len(valid_scalar)} complex={len(valid_complex)} unknown={len(unknown)}")
    return {"scalar": valid_scalar, "complex": valid_complex, "unknown": unknown, "other": other}


def main():
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0) as c:
        SKU = "583162"  # CAFE Olé Texas Pecan Ground Coffee (confirmed real)
        section(f"productDetail re-probe with REAL SKU {SKU} (FIXED logic)")

        template = '''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {
          productDetail(storeId: $s, id: $id, shoppingContext: $ctx) { __FIELD__ }
        }'''
        vars_ = {"s": "57", "id": SKU, "ctx": "CURBSIDE_PICKUP"}

        candidates = [
            # All the things we know exist from records[] view
            "id", "displayName", "inAssortment", "brand",
            "inventory", "availability", "breadcrumbs", "coupons",
            # Pricing
            "price", "priceInfo", "priceDetails", "pricing", "prices",
            "regularPrice", "salePrice", "currentPrice", "listPrice",
            "displayPrice", "displayedUnitPrice", "displayedPrice",
            "unitPrice", "perUnitPrice", "shelfPrice", "everydayPrice",
            "productPrice", "productPricing", "currentSellingPrice",
            # Images
            "image", "imageInfo", "primaryImage", "primaryImageUrl",
            "imageUrl", "imageUri", "imageSrc", "images", "imageGallery",
            "media", "thumbnail", "thumbnailUrl",
            "smallImage", "mediumImage", "largeImage",
            "productImage", "productImages", "productMedia",
            # URLs / slugs
            "url", "uri", "link", "slug", "productSlug",
            "path", "permalink", "canonicalUrl",
            "productUrl", "detailUrl", "pdpUrl", "webUrl",
            # Size / packaging
            "size", "sizeText", "packageSize", "containerSize",
            "unitSize", "displaySize", "displayedSize",
            "uom", "unitOfMeasure", "uomLabel", "uomDisplay",
            "weight", "netWeight", "productSize", "productUom",
            # Description / details
            "description", "shortDescription", "longDescription",
            "details", "productDetails", "productInformation",
            "info", "summary", "highlights",
            # Misc
            "aisle", "aisleLocation",
            "isOwnedBrand", "isPrivateLabel", "isHebOwnedBrand",
            "fulfillmentChannels", "channels",
            "tags", "labels", "badges",
            "snap", "snapEligible", "isSnapEligible", "ebt",
            "discontinued", "isDiscontinued",
            "active", "isActive",
            "ratings", "reviews", "averageRating",
            "ingredients", "nutrition", "nutritionFacts",
            "warnings", "allergens", "dietary",
            "minSellQuantity", "maxSellQuantity",
            "isAgeRestricted", "ageRestriction",
            # plain names that were "unknown" in old logic
            "name", "title", "fullName",
            "department", "category", "categoryId",
        ]
        result = probe_fixed(c, template, vars_, candidates, "productDetail")
        save("phase12_productDetail.json", result)

        # ============================================================
        # Big pull: query everything together
        # ============================================================
        section("Big productDetail query with all valid fields")
        if result["scalar"]:
            # Expand complex with __typename for now to see types
            scalar_part = "\n              ".join(result["scalar"])
            complex_part = "\n              ".join(
                [f'{c["field"]} {{ __typename }}' for c in result["complex"]]
            )
            qbig = f'''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {{
              productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {{
                __typename
                {scalar_part}
                {complex_part}
              }}
            }}'''
            r = post(c, qbig, vars_)
            print(f"  status: {r.status_code}")
            try:
                body = r.json()
                print(f"  body (truncated):\n{json.dumps(body, indent=2)[:8000]}")
            except Exception:
                print(f"  raw: {r.text[:4000]}")
            save("phase12_BIG_DUMP.json", r.text)

        # ============================================================
        # If complex fields exist, probe each one's subfields
        # ============================================================
        if result["complex"]:
            section("Probe subfields of all productDetail complex fields")
            generic_subfields = [
                "url", "uri", "src", "href",
                "value", "amount", "currency", "currencyCode",
                "price", "displayPrice", "regularPrice", "salePrice",
                "id", "name", "label", "displayName", "title",
                "state", "status", "level", "available",
                "small", "medium", "large", "primary",
                "alt", "altText", "description",
                "min", "max",
                "type", "kind",
                "categoryId", "categoryName",
                "text", "html", "raw",
                "isOnSale", "isPromo", "isDiscount",
                # For inventory we know inventoryState + quantity
                "inventoryState", "quantity",
            ]
            for cx in result["complex"]:
                fname, ftype = cx["field"], cx["type"]
                print(f"\n  --- {fname} ({ftype}) ---")
                sub_template = f'''query Q($s: ID!, $id: ID!, $ctx: ShoppingContext!) {{
                  productDetail(storeId: $s, id: $id, shoppingContext: $ctx) {{ {fname} {{ __FIELD__ }} }}
                }}'''
                sub_valid = []
                sub_complex = []
                for sf in generic_subfields:
                    q = sub_template.replace("__FIELD__", sf)
                    r = post(c, q, vars_)
                    try:
                        body = r.json()
                        if "errors" not in body:
                            sub_valid.append(sf)
                            data = body.get("data") or {}
                            pd = data.get("productDetail") or {}
                            v = pd.get(fname)
                            preview = json.dumps(v)[:140] if v else "null"
                            print(f"    ✓ {sf:20s} -> {preview}")
                        else:
                            em = body["errors"][0]["message"]
                            if "must have a selection of subfields" in em:
                                m = re.search(r"of type [\"']([\w!\[\]]+)[\"']", em)
                                inner = m.group(1) if m else "?"
                                sub_complex.append({"field": sf, "type": inner})
                                print(f"    ⊞ {sf:20s} NESTED type={inner}")
                    except Exception:
                        pass
                    time.sleep(0.08)
                save(f"phase12_sub_{fname}.json", {"scalar": sub_valid, "complex": sub_complex})

    print(f"\nDone.")


if __name__ == "__main__":
    main()
