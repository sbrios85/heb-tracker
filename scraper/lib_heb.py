"""
H-E-B Tracker — shared library
==============================
Reusable helpers for all the H-E-B scripts. Imported by:
  - heb_discover_brands.py
  - heb_discover_products.py
  - heb_refresh.py
"""

import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx


# =============================================================
# CONSTANTS
# =============================================================
GRAPHQL_ENDPOINT = "https://www.heb.com/graphql"
HOMEPAGE = "https://www.heb.com/"
WALDRON_STORE_NUMBER = 57  # 1145 Waldron Rd, Corpus Christi (Flour Bluff)
SHOPPING_CONTEXT = "CURBSIDE_PICKUP"  # the only enum value that works for productDetail

# H-E-B's store cookie. Found by inspecting the cookies set after picking
# a store on heb.com. The value is the store number.
# If this doesn't pin the store on PDPs, we'll add more methods.
STORE_COOKIE_NAME = "HEB_PREFERRED_STORE"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.heb.com",
    "Referer": "https://www.heb.com/",
}

# Polite throttling between requests. H-E-B doesn't seem to rate-limit
# us aggressively but we don't want to be rude. 0.6s = ~1.5 req/s.
DEFAULT_DELAY = 0.6


# =============================================================
# FILE PATHS
# =============================================================
REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data"
PRICES_DIR = DATA_DIR / "prices"
BRANDS_FILE = DATA_DIR / "brands.json"
PRODUCTS_FILE = DATA_DIR / "products.json"
PENDING_FILE = DATA_DIR / "pending.json"

# Ensure dirs exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
PRICES_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================
# CLIENT FACTORY
# =============================================================
def make_client(timeout: float = 30.0, set_store: bool = True) -> httpx.Client:
    """Create an httpx Client with our standard headers and cookies seeded.
    If set_store is True, attempt to pin the Waldron Rd store on the session."""
    c = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout)
    # Seed cookies by hitting the homepage
    c.get(HOMEPAGE)
    if set_store:
        _set_store_session(c, WALDRON_STORE_NUMBER)
    return c


def _set_store_session(client: httpx.Client, store_number: int) -> None:
    """Try to pin the store on the current session. We try a few approaches
    since H-E-B uses different mechanisms across pages.

    Strategy:
      1. Set the HEB_PREFERRED_STORE cookie directly.
      2. POST the UpdatePreferredStore GraphQL mutation (this is the
         operation name we saw in JS chunks — the input shape is unknown
         but a best-effort attempt is harmless if it fails).
    """
    # Approach 1: cookie
    client.cookies.set(STORE_COOKIE_NAME, str(store_number), domain=".heb.com")
    # Some sites also use these cookie names; set defensively
    client.cookies.set("preferredStore", str(store_number), domain=".heb.com")
    client.cookies.set("storeNumber", str(store_number), domain=".heb.com")

    # Approach 2: mutation (best-effort)
    mutation = """
      mutation UpdatePreferredStore($storeNumber: Int!) {
        updatePreferredStore(storeNumber: $storeNumber) {
          __typename
        }
      }
    """
    try:
        client.post(GRAPHQL_ENDPOINT, json={
            "query": mutation,
            "variables": {"storeNumber": store_number},
        })
    except Exception:
        pass


# =============================================================
# GRAPHQL
# =============================================================
def gql(client: httpx.Client, query: str, variables: Optional[dict] = None) -> dict:
    """POST a GraphQL query and return the parsed JSON. Raises on error."""
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    r = client.post(GRAPHQL_ENDPOINT, json=payload)
    body = r.json()
    if "errors" in body and body["errors"]:
        # Don't raise on every error — just attach. Callers can decide.
        body.setdefault("_status", r.status_code)
    return body


# =============================================================
# PRODUCT SEARCH (paginated)
# =============================================================
PRODUCT_SEARCH_QUERY = """
query Q($query: String!, $filter: String, $limit: Int, $offset: Int) {
  productSearch(
    storeId: %d
    shoppingContext: %s
    query: $query
    filter: $filter
    limit: $limit
    offset: $offset
  ) {
    total
    records {
      id
      displayName
      inAssortment
      brand { name isOwnBrand }
      inventory { inventoryState quantity }
      breadcrumbs { categoryId title }
    }
  }
}
""" % (WALDRON_STORE_NUMBER, SHOPPING_CONTEXT)


def product_search(client: httpx.Client, query: str, brand: Optional[str] = None,
                   limit: int = 60, offset: int = 0) -> dict:
    """Run productSearch with optional brand filter. Returns the productSearch dict."""
    filt = f"brand:{brand}" if brand else None
    vars_ = {"query": query, "limit": limit, "offset": offset}
    if filt is not None:
        vars_["filter"] = filt
    body = gql(client, PRODUCT_SEARCH_QUERY, vars_)
    if "errors" in body:
        return {"total": 0, "records": [], "_errors": body["errors"]}
    return (body.get("data") or {}).get("productSearch") or {"total": 0, "records": []}


# =============================================================
# BROWSE CATEGORY (paginated)
# =============================================================
BROWSE_CATEGORY_QUERY = """
query Q($categoryId: String!, $filter: String, $limit: Int, $offset: Int) {
  browseCategory(
    storeId: %d
    shoppingContext: %s
    categoryId: $categoryId
    filter: $filter
    limit: $limit
    offset: $offset
  ) {
    total
    records {
      id
      displayName
      brand { name isOwnBrand }
    }
    breadcrumbs { categoryId title }
  }
}
""" % (WALDRON_STORE_NUMBER, SHOPPING_CONTEXT)


def browse_category(client: httpx.Client, category_id: str,
                    brand: Optional[str] = None,
                    limit: int = 60, offset: int = 0) -> dict:
    filt = f"brand:{brand}" if brand else None
    vars_ = {"categoryId": category_id, "limit": limit, "offset": offset}
    if filt is not None:
        vars_["filter"] = filt
    body = gql(client, BROWSE_CATEGORY_QUERY, vars_)
    if "errors" in body:
        return {"total": 0, "records": [], "_errors": body["errors"]}
    return (body.get("data") or {}).get("browseCategory") or {"total": 0, "records": []}


# =============================================================
# PRODUCT PAGE FETCH + __NEXT_DATA__ EXTRACTION
# =============================================================
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)


def fetch_product_page(client: httpx.Client, product_id: str,
                       slug: Optional[str] = None) -> Optional[dict]:
    """Fetch a product page and return the __NEXT_DATA__.props.pageProps.product
    dict. If slug is unknown, use a placeholder — H-E-B normalizes to the
    canonical slug via redirect."""
    slug = slug or "x"
    url = f"https://www.heb.com/product-detail/{slug}/{product_id}"
    try:
        r = client.get(url)
    except Exception as e:
        return None
    if r.status_code != 200:
        return None
    m = NEXT_DATA_RE.search(r.text)
    if not m:
        return None
    try:
        nd = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return (nd.get("props") or {}).get("pageProps", {}).get("product")


# =============================================================
# JSON FILE HELPERS
# =============================================================
def load_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default if default is not None else {}


def save_json(path: Path, data) -> None:
    """Save JSON pretty-printed, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# =============================================================
# PRODUCT DATA EXTRACTION (from __NEXT_DATA__ product blob)
# =============================================================
def extract_product_summary(product: dict) -> dict:
    """Pull the fields we care about from the raw product blob into a flat dict.
    This is what gets stored in daily snapshots."""
    if not product:
        return {}

    # Price extraction: SKUs[0].contextPrices where context == "ONLINE"
    online_price = None
    online_sale_price = None
    is_on_sale = False
    unit_price = None
    unit_label = None
    skus = product.get("SKUs") or []
    if skus:
        sku0 = skus[0]
        for cp in (sku0.get("contextPrices") or []):
            if cp.get("context") == "ONLINE":
                lp = cp.get("listPrice") or {}
                sp = cp.get("salePrice") or {}
                ulp = cp.get("unitListPrice") or {}
                online_price = lp.get("amount")
                online_sale_price = sp.get("amount")
                is_on_sale = bool(cp.get("isOnSale"))
                unit_price = ulp.get("amount")
                unit_label = ulp.get("unit")
                break

    # Image: first medium-size product image
    image_url = None
    for img in (product.get("productImageUrls") or []):
        if img.get("size") == "MEDIUM":
            image_url = img.get("url")
            break
    if not image_url and product.get("productImageUrls"):
        image_url = product["productImageUrls"][0].get("url")

    # Brand
    brand = product.get("brand") or {}

    # Aisle
    loc = product.get("productLocation") or {}

    # Inventory
    inv = product.get("inventory") or {}

    # Coupons summary
    coupons = product.get("coupons") or []
    coupon_summaries = [
        {
            "id": c.get("id"),
            "short": c.get("shortDescription"),
            "description": c.get("description"),
            "expires": c.get("expirationDate"),
        }
        for c in coupons
    ]

    # Breadcrumb category names
    bcrumbs = product.get("breadcrumbs") or []
    category_path = " > ".join(b.get("title", "") for b in bcrumbs if b.get("title"))

    return {
        "id": product.get("id"),
        "displayName": product.get("fullDisplayName"),
        "brandName": brand.get("name"),
        "isOwnBrand": brand.get("isOwnBrand"),
        "url": product.get("productPageURL"),
        "imageUrl": image_url,
        "inventoryState": inv.get("inventoryState"),
        "inAssortment": product.get("inAssortment"),
        "aisle": loc.get("location"),
        "locationAvailability": loc.get("availability"),
        "onAd": product.get("onAd"),
        "isNew": product.get("isNew"),
        "isEbtSnap": product.get("isEbtSnapProduct"),
        "onlinePrice": online_price,
        "onlineSalePrice": online_sale_price,
        "isOnSale": is_on_sale,
        "unitPrice": unit_price,
        "unitLabel": unit_label,
        "categoryPath": category_path,
        "couponCount": len(coupons),
        "coupons": coupon_summaries,
        "storeIdReturned": product.get("storeId"),
    }


# =============================================================
# THROTTLE
# =============================================================
def polite_sleep(seconds: float = DEFAULT_DELAY) -> None:
    time.sleep(seconds)
