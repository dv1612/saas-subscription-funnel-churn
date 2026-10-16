"""
generate_mock_data.py
---------------------
Creates the mock SaaS dataset the rest of the project runs on.

Outputs (data/raw/):
    subscription_logs.csv  user_id, event_timestamp, funnel_stage
    subscriptions.csv      one row per paying customer: plan, MRR, status, dates

The generator is seeded, so every run reproduces the same numbers and the
figures quoted in the report stay valid.

Usage:
    python generate_mock_data.py [--users 25000] [--seed 42]
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

STAGES = ["Signup", "Profile", "First Use", "Paid Upgrade"]

# Acquisition channel -> (share of signups, stage-to-stage conversion multiplier)
CHANNELS = {
    "Organic Search": (0.34, 1.00),
    "Paid Social":    (0.26, 0.82),
    "Referral":       (0.22, 1.24),
    "Partner":        (0.18, 1.09),
}
# Baseline conversion into each stage, before the channel multiplier.
BASE_CONVERSION = {"Profile": 0.72, "First Use": 0.66, "Paid Upgrade": 0.40}

PLANS = {            # plan -> (share, monthly price, monthly churn hazard)
    "Starter": (0.52, 29.0, 0.055),
    "Growth":  (0.34, 99.0, 0.032),
    "Scale":   (0.14, 299.0, 0.018),
}

START = pd.Timestamp("2024-01-01")
END = pd.Timestamp("2025-06-30")
ANALYSIS_DATE = pd.Timestamp("2025-07-01")   # data cut-off used by every downstream step


def pick(rng, mapping, size):
    keys = list(mapping)
    probs = np.array([mapping[k][0] for k in keys], dtype=float)
    return rng.choice(keys, size=size, p=probs / probs.sum())


def build(users: int, seed: int):
    rng = np.random.default_rng(seed)

    user_ids = np.arange(100_001, 100_001 + users)
    channel = pick(rng, CHANNELS, users)

    # Signups ramp up over time - a growing company, not a flat one.
    span_days = (END - START).days
    weight = np.linspace(0.6, 1.8, span_days + 1)
    weight = weight / weight.sum()
    signup_day = rng.choice(span_days + 1, size=users, p=weight)
    signup_ts = (START + pd.to_timedelta(signup_day, unit="D")
                 + pd.to_timedelta(rng.integers(8 * 3600, 22 * 3600, users), unit="s"))

    mult = np.array([CHANNELS[c][1] for c in channel])
    # Product improved over time: later cohorts convert slightly better.
    maturity = 1 + 0.12 * (signup_day / span_days)

    events = [pd.DataFrame({"user_id": user_ids, "event_timestamp": signup_ts,
                            "funnel_stage": "Signup"})]
    reached = np.ones(users, dtype=bool)
    last_ts = signup_ts.copy()
    # Lag profile per stage, in hours: (shape, scale) of a gamma draw.
    lag = {"Profile": (1.6, 4.0), "First Use": (1.9, 36.0), "Paid Upgrade": (2.2, 160.0)}

    for stage in STAGES[1:]:
        p = np.clip(BASE_CONVERSION[stage] * mult * maturity, 0.02, 0.97)
        advance = reached & (rng.random(users) < p)
        shape, scale = lag[stage]
        hours = rng.gamma(shape, scale, users)
        ts = last_ts + pd.to_timedelta(hours, unit="h")
        events.append(pd.DataFrame({"user_id": user_ids[advance],
                                    "event_timestamp": ts[advance],
                                    "funnel_stage": stage}))
        last_ts = np.where(advance, ts, last_ts)
        last_ts = pd.to_datetime(pd.Series(last_ts))
        reached = advance

    logs = pd.concat(events, ignore_index=True)
    logs = logs[logs["event_timestamp"] < ANALYSIS_DATE]
    # Real logs arrive unsorted and carry the odd duplicate emit.
    logs = pd.concat([logs, logs.sample(frac=0.012, random_state=seed)], ignore_index=True)
    logs = logs.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    logs["event_timestamp"] = logs["event_timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")

    # --- paying customers -----------------------------------------------------
    paid = (logs.loc[logs["funnel_stage"] == "Paid Upgrade", ["user_id", "event_timestamp"]]
                .drop_duplicates("user_id"))
    paid["subscription_start"] = pd.to_datetime(paid["event_timestamp"])
    paid = paid.drop(columns="event_timestamp").reset_index(drop=True)
    n = len(paid)

    plan = pick(rng, PLANS, n)
    mrr = np.array([PLANS[p][1] for p in plan])
    hazard = np.array([PLANS[p][2] for p in plan])

    # Monthly churn draw, with elevated risk in the first three months.
    start_month = paid["subscription_start"].dt.to_period("M")
    months_possible = ((ANALYSIS_DATE.to_period("M") - start_month)
                       .apply(lambda x: x.n)).to_numpy()
    churn_month = np.full(n, -1)
    for m in range(0, months_possible.max() + 1):
        at_risk = (churn_month < 0) & (months_possible > m)
        early = 2.1 if m < 3 else (1.3 if m < 6 else 1.0)
        draw = rng.random(n) < (hazard * early)
        churn_month = np.where(at_risk & draw, m + 1, churn_month)

    churn_date = pd.Series(pd.NaT, index=range(n), dtype="datetime64[ns]")
    churned = churn_month > 0
    churn_date[churned] = [
        (start_month.iloc[i].to_timestamp() + pd.DateOffset(months=int(churn_month[i]))
         + pd.to_timedelta(int(rng.integers(0, 27)), unit="D"))
        for i in np.where(churned)[0]
    ]
    churn_date = churn_date.where(churn_date < ANALYSIS_DATE)

    subs = pd.DataFrame({
        "user_id": paid["user_id"].to_numpy(),
        "signup_date": paid["subscription_start"].dt.strftime("%Y-%m-%d"),
        "plan": plan,
        "mrr": mrr,
        "acquisition_channel": [channel[np.where(user_ids == u)[0][0]] for u in paid["user_id"]],
        "status": np.where(churn_date.notna(), "Churned", "Active"),
        "churn_date": churn_date.dt.strftime("%Y-%m-%d"),
    })
    return logs, subs


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the mock SaaS dataset.")
    ap.add_argument("--users", type=int, default=25_000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if args.users < 100:
        print("ERROR: --users must be at least 100.", file=sys.stderr)
        return 1

    out = Path(__file__).parent / "data/raw"
    out.mkdir(parents=True, exist_ok=True)
    logs, subs = build(args.users, args.seed)
    logs.to_csv(out / "subscription_logs.csv", index=False)
    subs.to_csv(out / "subscriptions.csv", index=False)

    print(f"subscription_logs.csv : {len(logs):,} events, {logs.user_id.nunique():,} users")
    print(f"subscriptions.csv     : {len(subs):,} paying customers "
          f"({(subs.status == 'Churned').mean():.1%} churned)")
    print(f"MRR on the book       : £{subs.loc[subs.status == 'Active', 'mrr'].sum():,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
