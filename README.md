# Marketing Measurement Copilot

**Which ad dollars actually drive sales?** Every ad platform claims credit for the same purchases, so online brands
overestimate what their ads do and put budget in the wrong places. This project is a prototype that estimates the
true return on each channel, recommends budget shifts, and answers questions in plain English, with every number
coming from a statistical model rather than an LLM.

> **Status: Week 1 of 10.** The data simulator is done. Pipeline, models, API and copilot are in progress.

## Why simulated data?

Real companies never know the true effect of their ads, which makes it impossible to check whether a measurement
model is right. So this project starts from a simulated brand, **Saguaro Supply Co.** (fictional), whose true
channel effects are known. The models never see that truth; tests and results compare against it.
Public real-world data (the Criteo uplift dataset) is used for the A/B-testing module.

## What the simulator produces

Two years (Oct 2024 to Sep 2026) of daily data across 10 US regions and 5 channels, about 320,000 orders and
57,000 ad-platform rows, exported the way a brand really receives them:

| File | Source it imitates | Built-in quirk |
| --- | --- | --- |
| `google_ads_daily.csv` | Google Ads | Campaign names with inconsistent casing and spaces |
| `meta_ads_daily.csv` | Meta Ads | Duplicate rows, missing impression counts |
| `tiktok_ads_daily.csv` | TikTok Ads | Spend stored as text, e.g. `$1,234.56` |
| `dsp_display_daily.csv` | Display DSP | UTC end-of-day timestamps (naive date cast is off by one day); missing days |
| `esp_email_daily.csv` | Email platform | Emails sent instead of impressions |
| `shop_orders.csv`, `shop_orders_late.csv` | Store backend | Some recent orders arrive in a later export |
| `shop_customers.csv`, `shop_products.csv`, `shop_price_history.csv` | Store backend | Prices change over time |
| `campaign_settings_log.csv` | Ad platforms' change history | Budgets, names and targeting change (feeds an SCD Type 2) |
| `geo_experiments.csv` | Brand's test log | A 4-week paid-social geo holdout |

Each quirk can be switched off in `simulator/config.yaml`, and the tests prove each one changes only its own file.

### How sales are generated

Each day's expected orders in each region are baseline demand (trend, weekly and yearly seasonality, holidays)
plus each channel's effect. A channel's effect carries over from day to day (**adstock**) and shows diminishing
returns (**saturation**, a Hill curve). Actual orders are Poisson draws around that expectation.

Ad platforms report conversions the way real ones do: inflated by view-through credit and by claiming purchases
that would have happened anyway.

### The truth the models must recover

| Channel | True ROI | ROAS the platform reports | True marginal ROI |
| --- | --- | --- | --- |
| Paid search | 2.00 | 3.48 | 1.08 |
| Paid social | 2.40 | 4.22 | 1.42 |
| TikTok | 0.80 | 1.98 | 0.36 |
| Display | 0.50 | 2.84 | 0.20 |
| Email | 7.00 | 16.75 | 4.15 |

On the platforms' own numbers every channel looks profitable. In truth, TikTok and display lose money, and the next
dollar in paid search earns far less than the average dollar. The geo holdout's true incremental ROAS is 2.44.

Ground truth is written to `data/ground_truth/` and is never read by any model.

## Quick start

```bash
uv sync                              # or: pip install -e ".[dev]"
python -m simulator.generate         # writes data/raw/ and data/ground_truth/
pytest -q                            # 12 tests on the simulator
```

Change the seed or any channel's true parameters in `simulator/config.yaml` and regenerate.

## Roadmap

| Weeks | Phase | Outcome |
| --- | --- | --- |
| 1 to 3 | Data foundation | Simulator, dbt models on BigQuery (star schema, SCD2, tests), Dagster schedule, dashboard |
| 4 to 6 | Measurement | Bayesian media mix model, geo-lift and CUPED experiments, lift-test calibration, budget optimizer |
| 7 to 8 | Production | FastAPI service in Docker, CI/CD with GitHub Actions, deployed on Cloud Run, Streamlit demo |
| 9 to 10 | Copilot | Tool-calling agent and MCP server over the API, with an evaluation harness |

## Repository layout

```
simulator/       synthetic brand, ground truth, export defects
tests/           tests for every layer
data/            generated, not committed: raw/, ground_truth/
```

More folders arrive with each phase: `warehouse/` (dbt), `orchestration/`, `measurement/`, `api/`, `copilot/`, `app/`.
