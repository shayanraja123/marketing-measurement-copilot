"""Generate a simulated direct-to-consumer brand with a known ground truth.

How sales are produced (per region r, day t):

    expected_orders[t, r] = baseline[t, r] + sum over channels of incremental_orders[c, t, r]

    incremental_revenue[c, t, r] = beta_c * w_r * hill(adstock(spend[c, :, r])[t] / (K_c * w_r))

* adstock: geometric carryover, a[t] = spend[t] + decay * a[t-1]
* hill:    diminishing returns, x**s / (x**s + 1), equals 0.5 when adstocked spend hits K
* beta_c is calibrated so the channel's true incremental ROAS (iROAS) matches `target_iroas` in the config

Actual orders are Poisson draws around the expectation, so the data is noisy the way real data is,
while the expected contribution of every channel (the "truth") is known exactly.

Usage:
    python -m simulator.generate                      # writes data/raw/ and data/ground_truth/
    python -m simulator.generate --write-clean        # also writes defect-free tables to data/clean/
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "simulator" / "config.yaml"

CATALOG = {
    "apparel": ["Trail Tee", "Sun Hoodie", "Canyon Shorts", "Merino Base Layer", "Rain Shell"],
    "footwear": ["Desert Trail Runner", "Approach Shoe", "Camp Sandal", "Hiking Boot", "Wool Socks 3-Pack"],
    "packs": ["Daypack 24L", "Hydration Vest", "Overnight Pack 45L", "Sling Bag", "Dry Bag Set"],
    "camp": ["Ultralight Tent", "Sleeping Quilt", "Camp Stove", "Headlamp", "Insulated Bottle"],
}

# Rough media economics used only to turn spend into impressions and clicks.
MEDIA = {
    "paid_search": {"cpm": 40.0, "ctr": 0.060},
    "paid_social": {"cpm": 12.0, "ctr": 0.012},
    "tiktok": {"cpm": 8.0, "ctr": 0.008},
    "display": {"cpm": 4.0, "ctr": 0.002},
    "email": {"cpm": 1.0, "ctr": 0.020},  # "impressions" are emails sent; cost is per 1,000 sends
}

# Share of orders by hour of day (local time), evening-heavy like most online stores.
HOUR_WEIGHTS = np.array([1, 0.6, 0.4, 0.3, 0.3, 0.5, 1, 2, 3, 3.5, 4, 4, 4.2, 4, 3.8, 3.8, 4, 4.5, 5, 6, 6.5, 6, 4, 2])


@dataclass
class SimulationResult:
    tables: dict[str, pd.DataFrame]
    truth: dict
    contributions: pd.DataFrame
    meta: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- building blocks


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def geometric_adstock(x: np.ndarray, decay: float) -> np.ndarray:
    """Carryover along axis 0. x has shape (days, regions)."""
    out = np.empty_like(x, dtype=float)
    carry = np.zeros(x.shape[1:])
    for t in range(x.shape[0]):
        carry = x[t] + decay * carry
        out[t] = carry
    return out


def hill(x: np.ndarray, shape: float) -> np.ndarray:
    """Saturation curve in [0, 1); x is adstocked spend divided by the half-saturation point."""
    xs = np.power(np.clip(x, 0, None), shape)
    return xs / (xs + 1.0)


def channel_response(spend_tr: np.ndarray, weights: np.ndarray, ch: dict, k_national: float) -> np.ndarray:
    """Share of maximum effect reached in each region-day (shape: days x regions)."""
    adstocked = geometric_adstock(spend_tr, ch["adstock_decay"])
    return hill(adstocked / (k_national * weights[None, :]), ch["hill_shape"])


def baseline_orders(cfg: dict, dates: pd.DatetimeIndex) -> np.ndarray:
    b = cfg["baseline"]
    t = np.arange(len(dates))
    growth = (1 + b["annual_growth"]) ** (t / 365.0)
    dow, doy = dates.day_of_week.to_numpy(), dates.day_of_year.to_numpy()
    month, day = dates.month.to_numpy(), dates.day.to_numpy()
    weekly = np.array(b["weekly_pattern"])[dow]
    # cosine peaking around day-of-year 350 (mid-December)
    yearly = 1 + b["yearly_amplitude"] * np.cos(2 * np.pi * (doy - 350) / 365.25)
    holiday = np.ones(len(dates))
    for h in b["holidays"]:
        mask = (month == h["month"]) & (day >= h["day_from"]) & (day <= h["day_to"])
        holiday[mask] *= h["multiplier"]
    return b["orders_per_day"] * growth * weekly * yearly * holiday


def planned_national_spend(cfg: dict, ch: dict, dates: pd.DatetimeIndex, rng) -> tuple[np.ndarray, list]:
    """Daily national spend for one channel, plus the budget regimes (for the campaign log)."""
    s = cfg["spend"]
    n = len(dates)
    regime = np.ones(n)
    regimes = []
    day = 0
    while day < n:
        length = int(rng.integers(s["regime_days"][0], s["regime_days"][1] + 1))
        mult = float(rng.uniform(*s["regime_range"]))
        regime[day : day + length] = mult
        regimes.append((day, mult))
        day += length
    q4 = np.where(dates.month >= 10, s["q4_multiplier"], 1.0)
    noise = rng.lognormal(-(s["noise_sd"] ** 2) / 2, s["noise_sd"], n)
    spend = ch["avg_daily_spend"] * regime * q4 * noise
    if "launch_day" in ch:
        spend[: ch["launch_day"]] = 0.0
    for a, b in ch.get("pauses", []):
        spend[a : b + 1] = 0.0
    return spend, regimes


def split_to_regions(national: np.ndarray, weights: np.ndarray, sd: float, rng) -> np.ndarray:
    noise = rng.lognormal(0.0, sd, (len(national), len(weights)))
    share = weights[None, :] * noise
    share /= share.sum(axis=1, keepdims=True)
    return national[:, None] * share


def build_products(cfg: dict, n_days: int, rng) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, pd.DataFrame]:
    p = cfg["products"]
    rows = []
    for cat in p["categories"]:
        for name in CATALOG[cat]:
            rows.append({"category": cat, "product_name": name})
    products = pd.DataFrame(rows).head(p["n_products"])
    products.insert(0, "product_id", [f"P{i + 1:03d}" for i in range(len(products))])
    base_price = np.round(rng.uniform(*p["price_range"], len(products))) - 0.01

    prices = np.tile(base_price, (n_days, 1))
    history = [
        {"product_id": pid, "price": price, "changed_on_day": 0} for pid, price in zip(products.product_id, base_price)
    ]
    for day in p["price_increase_days"]:
        chosen = rng.random(len(products)) < p["price_increase_share"]
        new = np.round(prices[day] * (1 + p["price_increase_pct"])) - 0.01
        for j in np.flatnonzero(chosen):
            prices[day:, j] = new[j]
            history.append({"product_id": products.product_id[j], "price": new[j], "changed_on_day": day})
    popularity = rng.dirichlet(np.full(len(products), 1.5))
    products["current_price"] = prices[-1]
    return products, prices, popularity, pd.DataFrame(history)


# --------------------------------------------------------------------------- main simulation


def simulate(cfg: dict) -> SimulationResult:
    rng = np.random.default_rng(cfg["seed"])
    dates = pd.date_range(cfg["start_date"], periods=cfg["n_days"], freq="D")
    n_days = len(dates)
    regions = list(cfg["regions"])
    weights = np.array([cfg["regions"][r] for r in regions], dtype=float)
    weights /= weights.sum()
    channels = cfg["channels"]

    # Products and expected order value per day
    products, prices, popularity, price_history = build_products(cfg, n_days, rng)
    q_probs = {int(k): v for k, v in cfg["orders"]["quantity_probs"].items()}
    expected_qty = sum(q * p for q, p in q_probs.items())
    aov = (prices @ popularity) * expected_qty  # expected revenue per order, per day

    # Baseline (organic) demand
    base_rt = baseline_orders(cfg, dates)[:, None] * weights[None, :]

    # Spend: planned (what the brand intended) and actual (after geo holdouts)
    planned, regimes = {}, {}
    for name, ch in channels.items():
        national, regimes[name] = planned_national_spend(cfg, ch, dates, rng)
        planned[name] = split_to_regions(national, weights, cfg["spend"]["region_noise_sd"], rng)
    actual = {name: arr.copy() for name, arr in planned.items()}
    for exp in cfg.get("geo_experiments", []):
        cols = [regions.index(r) for r in exp["treatment_regions"]]
        actual[exp["channel"]][exp["start_day"] : exp["end_day"] + 1, cols] = 0.0

    # Calibrate each channel so its true iROAS on planned spend equals target_iroas
    params = {}
    for name, ch in channels.items():
        national_adstock = geometric_adstock(planned[name].sum(axis=1)[:, None], ch["adstock_decay"])[:, 0]
        active = national_adstock[national_adstock > 0]
        k_national = ch["half_saturation"] * active.mean()
        resp = channel_response(planned[name], weights, ch, k_national)
        beta = ch["target_iroas"] * planned[name].sum() / (weights[None, :] * resp).sum()
        params[name] = {"k_national": float(k_national), "beta": float(beta)}

    def incremental_revenue(name: str, spend_tr: np.ndarray) -> np.ndarray:
        p = params[name]
        return p["beta"] * weights[None, :] * channel_response(spend_tr, weights, channels[name], p["k_national"])

    inc_rev = {name: incremental_revenue(name, actual[name]) for name in channels}
    inc_orders = {name: inc_rev[name] / aov[:, None] for name in channels}
    expected_orders = base_rt + sum(inc_orders.values())

    # ---------------------------------------------------------------- orders and customers
    n_orders_rt = rng.poisson(expected_orders)
    t_idx = np.repeat(np.repeat(np.arange(n_days), len(regions)), n_orders_rt.ravel())
    r_idx = np.repeat(np.tile(np.arange(len(regions)), n_days), n_orders_rt.ravel())
    n = len(t_idx)
    p_idx = rng.choice(len(products), size=n, p=popularity)
    qty = rng.choice(list(q_probs), size=n, p=list(q_probs.values()))
    unit_price = prices[t_idx, p_idx]
    hours = rng.choice(24, size=n, p=HOUR_WEIGHTS / HOUR_WEIGHTS.sum())
    seconds = hours * 3600 + rng.integers(0, 3600, n)
    order_ts = dates[t_idx] + pd.to_timedelta(seconds, unit="s")

    customer_ids = np.empty(n, dtype=object)
    customer_rows = []
    next_id = 1
    for r in range(len(regions)):
        idx = np.flatnonzero(r_idx == r)
        idx = idx[np.argsort(seconds[idx] + t_idx[idx] * 86400, kind="stable")]
        is_new = rng.random(len(idx)) >= cfg["orders"]["returning_customer_share"]
        is_new[0] = True
        new_count_before = np.cumsum(is_new) - is_new
        region_ids = np.array([f"C{next_id + k:07d}" for k in range(int(is_new.sum()))], dtype=object)
        next_id += int(is_new.sum())
        assigned = np.empty(len(idx), dtype=object)
        assigned[is_new] = region_ids
        returning = ~is_new
        assigned[returning] = region_ids[rng.integers(0, new_count_before[returning])]
        customer_ids[idx] = assigned
        firsts = idx[is_new]
        customer_rows.append(
            pd.DataFrame({"customer_id": region_ids, "region": regions[r], "first_order_ts": order_ts[firsts]})
        )

    orders = pd.DataFrame(
        {
            "order_ts": order_ts,
            "customer_id": customer_ids,
            "region": np.array(regions)[r_idx],
            "product_id": products.product_id.to_numpy()[p_idx],
            "quantity": qty,
            "unit_price": unit_price,
        }
    )
    orders["revenue"] = (orders.unit_price * orders.quantity).round(2)
    orders = orders.sort_values("order_ts", kind="stable").reset_index(drop=True)
    orders.insert(0, "order_id", [f"O{i + 1:08d}" for i in range(len(orders))])
    customers = pd.concat(customer_rows).sort_values("first_order_ts").reset_index(drop=True)

    # ---------------------------------------------------------------- ad platform data
    spend_rows, log_rows = [], []
    changes = cfg.get("campaign_changes", [])
    for name, ch in channels.items():
        media = MEDIA[name]
        reported_expected = inc_orders[name] * (1 + ch["view_through"]) + ch["baseline_capture"] * base_rt * (
            actual[name] > 0
        )
        for camp in ch["campaigns"]:
            share = camp["share"]
            # campaign attributes over time (for the SCD2 dimension)
            current = {"campaign_name": camp["name"], "targeting": camp["targeting"]}
            events = {d: {"daily_budget": round(ch["avg_daily_spend"] * share * m, -1)} for d, m in regimes[name]}
            for c in changes:
                if c["id"] == camp["id"]:
                    upd = {k2: v for k2, v in c.items() if k2 in ("name", "targeting")}
                    if "name" in upd:
                        upd["campaign_name"] = upd.pop("name")
                    events.setdefault(c["day"], {}).update(upd)
            start = ch.get("launch_day", 0)
            budget = None
            name_by_day = np.empty(n_days, dtype=object)
            for d in sorted(events):
                ev = events[d]
                budget = ev.get("daily_budget", budget)
                current = {**current, **{k2: v for k2, v in ev.items() if k2 != "daily_budget"}}
                name_by_day[d:] = current["campaign_name"]
                log_rows.append(
                    {
                        "campaign_id": camp["id"],
                        "channel": name,
                        **current,
                        "daily_budget": budget,
                        "updated_at": dates[max(d, start)] + pd.Timedelta(hours=9),
                    }
                )

            spend = actual[name] * share
            conv_mu = reported_expected * share
            imps = rng.poisson(spend / media["cpm"] * 1000)
            clicks = rng.binomial(imps, media["ctr"])
            conv = rng.poisson(conv_mu)
            conv_value = conv * aov[:, None] * rng.lognormal(0, 0.05, spend.shape)
            t_grid, r_grid = np.nonzero(spend > 0)
            spend_rows.append(
                pd.DataFrame(
                    {
                        "date": dates[t_grid],
                        "channel": name,
                        "campaign_id": camp["id"],
                        "campaign_name": name_by_day[t_grid],
                        "region": np.array(regions)[r_grid],
                        "spend": spend[t_grid, r_grid].round(2),
                        "impressions": imps[t_grid, r_grid],
                        "clicks": clicks[t_grid, r_grid],
                        "platform_conversions": conv[t_grid, r_grid],
                        "platform_conversion_value": conv_value[t_grid, r_grid].round(2),
                    }
                )
            )
    ad_spend = pd.concat(spend_rows).sort_values(["date", "channel", "campaign_id", "region"]).reset_index(drop=True)
    campaign_log = (
        pd.DataFrame(log_rows)
        .drop_duplicates(subset=["campaign_id", "updated_at"], keep="last")
        .sort_values(["campaign_id", "updated_at"])
        .reset_index(drop=True)
    )
    # the log only records real changes
    attrs = ["campaign_name", "targeting", "daily_budget"]
    changed = campaign_log.groupby("campaign_id")[attrs].transform(lambda s: s.ne(s.shift())).any(axis=1)
    campaign_log = campaign_log[changed].reset_index(drop=True)

    price_history["valid_from"] = dates[price_history.changed_on_day].date
    price_history = price_history.drop(columns="changed_on_day")

    region_table = pd.DataFrame(
        {"region": regions, "region_name": [r.replace("_", " ").title() for r in regions], "timezone": "America/Phoenix"}
    )
    experiments = pd.DataFrame(
        [
            {
                "experiment_id": e["id"],
                "channel": e["channel"],
                "treatment_regions": ";".join(e["treatment_regions"]),
                "start_date": dates[e["start_day"]].date(),
                "end_date": dates[e["end_day"]].date(),
                "design": "geo holdout: channel switched off in treatment regions",
            }
            for e in cfg.get("geo_experiments", [])
        ]
    )

    tables = {
        "ad_spend": ad_spend,
        "orders": orders,
        "customers": customers,
        "products": products,
        "price_history": price_history,
        "campaign_log": campaign_log,
        "regions": region_table,
        "geo_experiments": experiments,
    }

    # ---------------------------------------------------------------- ground truth
    truth = {"brand": "Saguaro Supply Co. (fictional)", "seed": cfg["seed"], "channels": {}, "experiments": {}}
    base_rev = base_rt * aov[:, None]
    total_expected_rev = base_rev.sum() + sum(v.sum() for v in inc_rev.values())
    truth["baseline_revenue_share"] = round(float(base_rev.sum() / total_expected_rev), 4)
    truth["total_expected_revenue"] = round(float(total_expected_rev), 2)
    for name, ch in channels.items():
        spend_total = actual[name].sum()
        contribution = inc_rev[name].sum()
        bumped = incremental_revenue(name, actual[name] * 1.01).sum()
        reported_value = (inc_orders[name] * (1 + ch["view_through"]) + ch["baseline_capture"] * base_rt * (
            actual[name] > 0
        )) * aov[:, None]
        truth["channels"][name] = {
            "total_spend": round(float(spend_total), 2),
            "true_incremental_revenue": round(float(contribution), 2),
            "true_iroas": round(float(contribution / spend_total), 4),
            "true_marginal_iroas": round(float((bumped - contribution) / (0.01 * spend_total)), 4),
            "expected_platform_roas": round(float(reported_value.sum() / spend_total), 4),
            "target_iroas": ch["target_iroas"],
            "adstock_decay": ch["adstock_decay"],
            "hill_shape": ch["hill_shape"],
            "half_saturation_national_usd": round(params[name]["k_national"], 2),
            "beta_max_daily_revenue_usd": round(params[name]["beta"], 2),
        }
    for exp in cfg.get("geo_experiments", []):
        name = exp["channel"]
        cols = [regions.index(r) for r in exp["treatment_regions"]]
        a, b = exp["start_day"], min(exp["end_day"] + exp["carryover_days"], n_days - 1)
        counterfactual = incremental_revenue(name, planned[name])
        lost = (counterfactual - inc_rev[name])[a : b + 1, cols].sum()
        withheld = (planned[name] - actual[name])[:, cols].sum()
        cf_total = base_rev[a : b + 1, cols].sum() + sum(
            (counterfactual if c == name else inc_rev[c])[a : b + 1, cols].sum() for c in channels
        )
        truth["experiments"][exp["id"]] = {
            "channel": name,
            "treatment_regions": exp["treatment_regions"],
            "window": [str(dates[a].date()), str(dates[b].date())],
            "true_revenue_lost": round(float(lost), 2),
            "spend_withheld": round(float(withheld), 2),
            "true_iroas": round(float(lost / withheld), 4),
            "true_lift_pct_of_revenue": round(float(100 * lost / cf_total), 3),
        }

    frames = []
    for r, region in enumerate(regions):
        frames.append(pd.DataFrame({"date": dates.date, "region": region, "channel": "baseline", "expected_revenue": base_rev[:, r]}))
        for name in channels:
            frames.append(
                pd.DataFrame({"date": dates.date, "region": region, "channel": name, "expected_revenue": inc_rev[name][:, r]})
            )
    contributions = pd.concat(frames, ignore_index=True)
    contributions["expected_revenue"] = contributions.expected_revenue.round(4)

    return SimulationResult(
        tables=tables,
        truth=truth,
        contributions=contributions,
        meta={"dates": dates, "regions": regions, "aov": aov},
    )


# --------------------------------------------------------------------------- CLI


def write_outputs(result: SimulationResult, cfg: dict, out_dir: Path, write_clean: bool = False) -> dict[str, Path]:
    from simulator.messiness import build_exports

    raw_dir = out_dir / "raw"
    truth_dir = out_dir / "ground_truth"
    raw_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    written = {}
    for fname, df in build_exports(result.tables, cfg.get("messiness", {}), cfg["seed"] + 1).items():
        path = raw_dir / fname
        df.to_csv(path, index=False)
        written[fname] = path

    with open(truth_dir / "ground_truth.json", "w") as f:
        json.dump(result.truth, f, indent=2)
    result.contributions.to_csv(truth_dir / "true_daily_contributions.csv", index=False)

    if write_clean:
        clean_dir = out_dir / "clean"
        clean_dir.mkdir(parents=True, exist_ok=True)
        for name, df in result.tables.items():
            df.to_csv(clean_dir / f"{name}.csv", index=False)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out", default=str(ROOT / "data"))
    parser.add_argument("--write-clean", action="store_true", help="also write defect-free tables to <out>/clean/")
    args = parser.parse_args()

    cfg = load_config(args.config)
    result = simulate(cfg)
    written = write_outputs(result, cfg, Path(args.out), args.write_clean)

    print(f"Wrote {len(written)} raw files to {Path(args.out) / 'raw'}")
    print(f"Orders: {len(result.tables['orders']):,}  |  ad-spend rows: {len(result.tables['ad_spend']):,}")
    print("\nTrue incremental ROAS vs what the platforms report:")
    for name, t in result.truth["channels"].items():
        print(f"  {name:<12} true iROAS {t['true_iroas']:>5.2f}   platform ROAS {t['expected_platform_roas']:>5.2f}")


if __name__ == "__main__":
    main()
