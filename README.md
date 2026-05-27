# heb-tracker

Daily price + availability tracker for H-E-B house-brand products.

## What this does

- Discovers every H-E-B house-brand SKU (HEB, Central Market, Hill Country Fair, H-E-B Select Ingredients, Texas Tough, Primo Picks, etc.).
- Pulls price, availability, image, and details for each, anchored to a specific store: **H-E-B at 1145 Waldron Rd, Corpus Christi, TX 78418**.
- Re-checks daily via GitHub Actions, flagging price changes and discontinuations.
- Dashboard tab to browse, filter, and approve new products into the tracking set.

## Architecture

Mirrors the existing real estate lead pipeline:

```
heb-tracker/
├── scraper/
│   ├── probe_heb_graphql.py     # one-time discovery of API shape (this file)
│   ├── heb_discover.py          # one-time: enumerate all house-brand SKUs
│   └── heb_refresh.py           # daily: re-check prices + availability
├── data/
│   ├── heb_products.json        # canonical product list (tracked SKUs)
│   ├── heb_pending.json         # new products awaiting approval
│   └── heb_price_history.json   # per-SKU price history
├── dashboard/
│   └── index.html               # browse, filter, approve
└── .github/workflows/
    ├── heb_refresh.yml          # daily 07:00 UTC
    └── heb_dashboard.yml        # GitHub Pages deploy
```

## How H-E-B exposes data

heb.com is a fully client-rendered Apollo/GraphQL app. The frontend hits `https://www.heb.com/graphql`. The endpoint is publicly accessible — no auth needed for product browsing — and other projects (alfredopzr/heb-scraper, mgwalkerjr95/texas-grocery-mcp, heb-sdk-unofficial) have already proven it works.

The endpoint requires a **store context** (storeId) for prices and availability, since H-E-B pricing varies by store.

## Step 0: run the probe

Before building anything else, we need to confirm the API shape from a real residential IP (not from CI or a sandbox, since some endpoints filter by user-agent/origin).

```bash
pip install httpx
python scraper/probe_heb_graphql.py
```

This will produce `./probe_output/` with response bodies for several candidate queries. Paste the contents back so we can finalize the real `heb_discover.py` against the actual schema H-E-B uses.

## Status

🚧 Pre-build. Step 0 (probe) ready to run.
