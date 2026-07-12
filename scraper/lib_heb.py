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
BLOCKLIST_FILE = DATA_DIR / "brand_blocklist.json"
TRACKED_FILE = DATA_DIR / "tracked.json"
EXTRA_BRANDS_FILE = DATA_DIR / "extra_brands.json"

# Ensure dirs exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
PRICES_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================
# CLIENT FACTORY
# =============================================================
def make_client(timeout: float = 30.0) -> httpx.Client:
    """Create an httpx Client with our standard headers and cookies seeded.

    Note: we do NOT try to pin a store on the session — that's tied to a
    logged-in User account and doesn't work anonymously. Instead, every
    product fetch passes an explicit storeId via get_product_by_id(), which
    is the reliable way to get Waldron (#57) data.
    """
    c = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout)
    c.get(HOMEPAGE)
    return c


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


def load_blocked_brands() -> set:
    """Return the set of blocked brand names from brand_blocklist.json.
    Discovery scripts call this to skip brands the user has excluded.
    Returns an empty set if the file doesn't exist."""
    data = load_json(BLOCKLIST_FILE, default=None)
    if not data:
        return set()
    return set(data.get("blocked_brands") or [])


def load_dismissed_ids() -> set:
    """Return the set of product IDs the user has dismissed (from tracked.json).
    Discovery + full-fetch call this to skip products the user never wants to
    see again. Returns an empty set if tracked.json doesn't exist or has no
    dismissed list (safe fallback — nothing gets skipped)."""
    data = load_json(TRACKED_FILE, default=None)
    if not data:
        return set()
    return set(str(x) for x in (data.get("dismissed") or []))


def load_extra_brands() -> set:
    """Return the set of NON-H-E-B brand names the user wants to track anyway
    (from extra_brands.json). These get promoted into the discovered brand list
    alongside house brands. Names must match H-E-B's exact spelling. Returns an
    empty set if the file doesn't exist."""
    data = load_json(EXTRA_BRANDS_FILE, default=None)
    if not data:
        return set()
    return set(data.get("extra_brands") or [])


# =============================================================
# GET PRODUCT BY ID (GraphQL — explicit storeId, the CORRECT path)
# =============================================================
# getProductById accepts an explicit storeId, so we always get the store
# we want (Waldron #57) regardless of session/login. This is far better
# than scraping the product page HTML, which defaults to store 92.
GET_PRODUCT_BY_ID_QUERY = """
query GetProductById($id: String!, $storeId: String) {
  getProductById(id: $id, storeId: $storeId) {
    id
    fullDisplayName
    productDescription
    productPageURL
    inAssortment
    isEbtSnapProduct
    onAd
    isNew
    minimumOrderQuantity
    maximumOrderQuantity
    ingredientStatement
    brand { name isOwnBrand }
    breadcrumbs { categoryId title }
    inventory { inventoryState }
    productLocation { location availability }
    productImageUrls { url size }
    # NOTE: coupons deferred. To add later, use coupons(storeId: $storeIdInt) {...}
    # — the field requires its own storeId: Int! arg. CouponV2 has id,
    # shortDescription, description, imageUrl, type, printStatuses (but NOT
    # expirationDate — bisect CouponV2 subfields to find the right date field).
    SKUs {
      id
      contextPrices {
        context
        isOnSale
        isPriceCut
        listPrice { amount formattedAmount unit }
        salePrice { amount formattedAmount unit }
        unitListPrice { amount formattedAmount unit }
      }
    }
  }
}
"""


def get_product_by_id(client: httpx.Client, product_id: str,
                      store_id: int = WALDRON_STORE_NUMBER) -> Optional[dict]:
    """Fetch full product data via GraphQL with an explicit storeId.
    Returns the getProductById object, or None on error."""
    body = gql(client, GET_PRODUCT_BY_ID_QUERY, {
        "id": str(product_id),
        "storeId": str(store_id),
    })
    if "errors" in body and body.get("errors"):
        return None
    return (body.get("data") or {}).get("getProductById")


def get_product_by_id_resilient(client: httpx.Client, product_id: str,
                                store_id: int = WALDRON_STORE_NUMBER,
                                max_retries: int = 3,
                                backoff_base: float = 3.0) -> tuple:
    """Like get_product_by_id but retries on None (which may be a soft
    rate-limit false-empty). Returns (product_or_None, was_throttled).

    H-E-B soft-throttles after sustained rapid requests — it returns errors
    or empties rather than 429. On a miss we wait (exponential backoff) and
    retry. If all retries miss, we report was_throttled=True so the caller
    can take a longer pause.
    """
    for attempt in range(max_retries):
        result = get_product_by_id(client, product_id, store_id)
        if result is not None:
            return result, False
        # Miss — back off and retry
        if attempt < max_retries - 1:
            wait = backoff_base * (2 ** attempt)  # 3s, 6s, 12s
            time.sleep(wait)
    # All retries exhausted
    return None, True



# =============================================================
# PRODUCT DATA EXTRACTION (from __NEXT_DATA__ product blob)
# =============================================================
def extract_size_from_text(name: str, description: str) -> str:
    """Best-effort extraction of a package size like '12 oz' or '6 ct' from
    the product name and description. Returns '' when nothing parseable found.

    H-E-B does not expose size as a GraphQL field, so this is our fallback
    for card display. For Listed products the user can enter size manually
    (100% accurate). This regex handles the common cases (coffee '12 oz',
    chips, drinks) but will legitimately return '' for many products where
    size isn't in the text (e.g. '1% Maximum Strength Hydrocortisone').
    """
    name = name or ""
    description = description or ""

    # Units we care about, in rough priority order. Longer/more-specific first.
    # Value + unit, e.g. "12 oz", "1.5 lb", "6 ct", "12 fl oz", "750 ml"
    unit_pattern = (
        r'(\d+(?:\.\d+)?)\s*'
        r'(fl\.?\s*oz|oz|lb|lbs|ct|count|pk|pack|g\b|kg|ml|mL|l\b|liter|'
        r'gal|gallon|qt|quart|pt|pint|piece|pieces|each|ea\b)'
    )

    def normalize(num, unit):
        unit = unit.lower().strip().rstrip('.')
        # Normalize common variants
        unit_map = {
            "fl oz": "fl oz", "floz": "fl oz", "fl.oz": "fl oz",
            "lbs": "lb", "count": "ct", "pack": "pk",
            "pieces": "piece", "ea": "each",
            "liter": "l", "gallon": "gal", "quart": "qt", "pint": "pt",
        }
        unit = unit_map.get(unit, unit)
        # Drop trailing .0
        if num.endswith(".0"):
            num = num[:-2]
        return f"{num} {unit}"

    # Search NAME first (higher precision — size in a product name is
    # almost always the real package size), then description.
    for source in (name, description):
        # Skip "1%" style concentration matches: require the unit to be a real
        # size unit, and reject if immediately preceded by "%".
        for m in re.finditer(unit_pattern, source, re.I):
            # Reject percentage: "1% Maximum" — check char before match
            start = m.start()
            preceding = source[max(0, start-1):start]
            following = source[m.end():m.end()+1]
            if following == "%":
                continue
            num, unit = m.group(1), m.group(2)
            return normalize(num, unit)
    return ""


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

    # Best-effort size extraction from name + description
    full_name = product.get("fullDisplayName") or ""
    description = product.get("productDescription") or ""
    size = extract_size_from_text(full_name, description)

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
        "size": size,
        "productDescription": description,
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


# =============================================================
# IMAGE HASHING (detect packaging changes when URL stays the same)
# =============================================================
def hash_image_bytes(client: httpx.Client, image_url: str) -> Optional[str]:
    """Fetch an image and return a short sha256 of its bytes, or None on failure."""
    if not image_url:
        return None
    try:
        r = client.get(image_url, timeout=20.0)
        if r.status_code != 200 or not r.content:
            return None
        import hashlib
        return hashlib.sha256(r.content).hexdigest()[:16]
    except Exception:
        return None
