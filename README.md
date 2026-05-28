[README.md](https://github.com/user-attachments/files/28313909/README.md)
# heb-tracker

Daily price + availability tracker for H-E-B house-brand products, anchored to the **Flour Bluff H-E-B plus! at 1145 Waldron Rd, Corpus Christi (store #57)**.

## Architecture

- **Discovery (GraphQL):** `productSearch` and `browseCategory` against `heb.com/graphql` to enumerate brands and products.
- **Detail (HTML):** parse `__NEXT_DATA__.props.pageProps.product` from each product page for full price/inventory/image/aisle data.

## Scripts

```
scraper/
├── lib_heb.py                  # shared helpers (GraphQL, parsing, throttle)
├── heb_discover_brands.py      # one-time: enumerate all brands
├── heb_discover_products.py    # one-time: enumerate products per brand   (TODO)
├── heb_refresh.py              # daily: refresh prices + availability    (TODO)
└── probe_heb_graphql.py        # historical: schema discovery (kept for reference)
```

## Data files

```
data/
├── brands.json                 # brand catalog: name, isOwnBrand, sample count
├── products.json               # canonical product list (TODO)
├── pending.json                # newly-seen products awaiting approval (TODO)
└── prices/
    └── YYYY-MM-DD.json         # daily snapshot of price + inventory (TODO)
```

## Workflows

| Workflow | Trigger | Purpose |
|---|---|---|
| `discover_brands.yml` | manual | Run once to enumerate all H-E-B brands found in the catalog |
| `discover_products.yml` | manual | (TODO) Enumerate every product under selected brands |
| `refresh.yml` | daily 07:00 UTC | (TODO) Refresh prices + flag changes |
| `probe.yml` | manual | (historical) Schema discovery — no longer needed |

## Step 1: Discover brands

1. Go to **Actions** → **Discover Brands** → **Run workflow**
2. Waits ~5-15 minutes (samples up to 15,000 products under the "Shop" root)
3. Commits `data/brands.json` to the repo
4. Open `data/brands.json` — the `own_brands` list at the top is the H-E-B house brand catalog

Then we'll write `heb_discover_products.py` to enumerate every product under those brands.

