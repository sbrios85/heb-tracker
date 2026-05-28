"""
Probe v4: diagnose why the production getProductById query returns None.

Runs the EXACT production query and prints the actual GraphQL error,
then bisects to find the bad field(s).
"""

import json
import sys
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).parent))
from lib_heb import HEADERS, HOMEPAGE, GRAPHQL_ENDPOINT, GET_PRODUCT_BY_ID_QUERY

TEST_ID = "583162"
TARGET = "57"


def fresh_client():
    c = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0)
    c.get(HOMEPAGE)
    return c


def section(t):
    print(f"\n{'=' * 70}\n  {t}\n{'=' * 70}")


def post(c, q, v=None):
    payload = {"query": q}
    if v is not None:
        payload["variables"] = v
    return c.post(GRAPHQL_ENDPOINT, json=payload)


def main():
    c = fresh_client()

    section("1) Run the EXACT production query, show full error")
    r = post(c, GET_PRODUCT_BY_ID_QUERY, {"id": TEST_ID, "storeId": TARGET})
    body = r.json()
    if "errors" in body:
        print(f"  ERRORS ({len(body['errors'])}):")
        for e in body["errors"]:
            print(f"    - {e.get('message')}")
    else:
        print("  NO ERRORS — query is fine! Data returned:")
        print(json.dumps(body, indent=2)[:600])

    section("2) Bisect: test each field/block individually")
    field_blocks = [
        "id",
        "fullDisplayName",
        "productDescription",
        "productPageURL",
        "inAssortment",
        "isEbtSnapProduct",
        "onAd",
        "isNew",
        "minimumOrderQuantity",
        "maximumOrderQuantity",
        "ingredientStatement",
        "brand { name isOwnBrand }",
        "breadcrumbs { categoryId title }",
        "inventory { inventoryState }",
        "productLocation { location availability }",
        "productImageUrls { url size }",
        "coupons { id shortDescription description expirationDate }",
        "coupons { id }",
        "SKUs { id }",
        "SKUs { id contextPrices { context isOnSale } }",
        "SKUs { id contextPrices { context isOnSale isPriceCut } }",
        "SKUs { id contextPrices { listPrice { amount formattedAmount unit } } }",
    ]
    for fb in field_blocks:
        q = f'query Q($id: String!, $s: String) {{ getProductById(id: $id, storeId: $s) {{ {fb} }} }}'
        r = post(c, q, {"id": TEST_ID, "s": TARGET})
        body = r.json()
        if "errors" in body:
            print(f"  ✗ {fb[:55]:55s} ERR: {body['errors'][0]['message'][:90]}")
        else:
            print(f"  ✓ {fb[:55]:55s} OK")

    section("3) Minimal known-good query (for the fix)")
    minimal = """
    query Q($id: String!, $s: String) {
      getProductById(id: $id, storeId: $s) {
        id
        fullDisplayName
        productDescription
        productPageURL
        inAssortment
        isEbtSnapProduct
        onAd
        isNew
        ingredientStatement
        brand { name isOwnBrand }
        breadcrumbs { categoryId title }
        inventory { inventoryState }
        productLocation { location availability }
        productImageUrls { url size }
        SKUs {
          id
          contextPrices {
            context
            isOnSale
            listPrice { amount formattedAmount unit }
            salePrice { amount formattedAmount unit }
            unitListPrice { amount formattedAmount unit }
          }
        }
      }
    }
    """
    r = post(c, minimal, {"id": TEST_ID, "s": TARGET})
    body = r.json()
    if "errors" in body:
        print(f"  minimal STILL errors: {body['errors'][0]['message']}")
    else:
        print(f"  minimal OK — this is the safe query")


if __name__ == "__main__":
    main()
