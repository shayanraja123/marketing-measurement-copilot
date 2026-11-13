# Marketing Measurement Copilot

**Which ad dollars actually drive sales?** Every ad platform claims credit for the same purchases, so online brands
overestimate what their ads do and put budget in the wrong places. This project is a prototype that estimates the
true return on each channel, recommends budget shifts, and answers questions in plain English, with every number
coming from a statistical model rather than an LLM.

> **Status: Week 2 of 11.** The data simulator and the dbt warehouse are done (running on DuckDB).
> Next: BigQuery and a daily Dagster schedule, then the measurement models.

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

| Channel | True iROAS | ROAS the platform reports | True marginal iROAS |
| --- | --- | --- | --- |
| Paid search | 2.00 | 3.48 | 1.08 |
| Paid social | 2.40 | 4.22 | 1.42 |
| TikTok | 0.80 | 1.98 | 0.36 |
| Display | 0.50 | 2.84 | 0.20 |
| Email | 7.00 | 16.75 | 4.15 |

On the platforms' own numbers every channel looks profitable. In truth, TikTok and display lose money, and the next
dollar in paid search earns far less than the average dollar. The geo holdout's true incremental ROAS is 2.44.

**Terms used here:** *incremental ROAS (iROAS)* is revenue the ads actually caused per $1 spent. *Platform ROAS* is revenue the ad platform claims per $1 spent. *Marginal iROAS* is what the next dollar earns.

Ground truth is written to `data/ground_truth/` and is never read by any model.

## Data warehouse (dbt)

The messy exports are cleaned and modeled with dbt into a tested star schema. Its final table,
`mart_mmm_daily`, is the single input the media mix model reads: one row per day and region with actual store
revenue and each channel's spend. Platform-reported conversions are deliberately left out of it.

![dbt lineage graph: raw files flow through staging and marts into the MMM input table](docs/images/dbt-lineage.png)

| Layer | Models | What happens there |
| --- | --- | --- |
| Staging | 12 views, one per raw file | Rename columns, set types explicitly, fix each export's defect |
| Intermediate | `int_ad_spend__unioned` | All five ad platforms stacked into one shape |
| Marts | `fct_ad_spend_daily`, `fct_orders` | Facts: spend per day, campaign and region; every store order |
| Marts | `dim_campaigns` (SCD Type 2), `dim_date`, `dim_regions`, `dim_products`, `dim_geo_experiment_regions` | Dimensions; campaigns keep every version of their name, targeting and budget |
| Marts | `mart_mmm_daily` | Model input: 7,300 rows (730 days x 10 regions) |

**Key design decisions**

- **Raw columns are read as text and typed explicitly in staging,** so a bad value fails loudly instead of being guessed.
- **Duplicates are removed on the table's grain** (day, campaign, region), not with `distinct`, because platforms revise numbers after the fact.
- **Missing stays missing.** Blank impressions stay NULL, and display's skipped export days are flagged rather than filled with zero spend.
- **Joins use IDs, never names.** Spend links to the campaign version live that day, so names and settings come from the official change log.
- **The SCD Type 2 dimension is built from the platforms' change log** rather than a dbt snapshot, because the full history is already available.

**Data quality:** 110 tests run on every build: keys, relationships between tables, no negative money, exactly one
current version per campaign, and reconciliation tests proving the MMM table holds exactly the same spend and revenue
as the fact tables. One test is set to warn rather than fail: 81 Meta rows have blank impression counts.

## Quick start

```bash
pip install -e ".[dev]"              # simulator, tests and dbt
python -m simulator.generate         # writes data/raw/ and data/ground_truth/
pytest -q                            # 12 tests on the simulator

cd warehouse
dbt build                            # 21 models and 110 data tests on a local DuckDB file
dbt docs generate && dbt docs serve  # browse the models and the lineage graph
```

Change the seed or any channel's true parameters in `simulator/config.yaml` and regenerate.

## Roadmap

| Weeks | Phase | Outcome |
| --- | --- | --- |
| 1 to 3 | Data foundation | Simulator, dbt models on BigQuery (star schema, SCD2, tests), Dagster schedule |
| 4 to 6 | Measurement | Bayesian media mix model, geo-lift and CUPED experiments, lift-test calibration, budget optimizer |
| 7 to 8 | Production | FastAPI service in Docker, CI/CD with GitHub Actions, deployed on Cloud Run, Streamlit demo |
| 9 to 11 | Copilot | Tool-calling agent over the API, RAG over the team's reports and notes, evaluation harness in CI |

## Repository layout

```
simulator/       synthetic brand, ground truth, export defects
tests/           tests for the simulator
warehouse/       dbt project: staging, marts, seeds and data tests
docs/images/     screenshots used in this README
data/            generated, not committed: raw/, ground_truth/, warehouse.duckdb
```

More folders arrive with each phase: `orchestration/`, `measurement/`, `api/`, `copilot/`, `app/`.
