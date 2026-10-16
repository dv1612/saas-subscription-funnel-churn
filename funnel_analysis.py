"""
funnel_analysis.py
------------------
Measures the SaaS acquisition funnel from raw event logs.

Reads  : data/raw/subscription_logs.csv  (user_id, event_timestamp, funnel_stage)
Writes : data/clean/funnel_metrics.csv
         data/clean/funnel_user_stages.csv   (one row per user, wide)
         reports/funnel_chart.png

Usage:
    python funnel_analysis.py
    python funnel_analysis.py --input data/raw/subscription_logs.csv --min-stage-users 50
"""

import argparse
import logging
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

LOGGER = logging.getLogger("funnel")

STAGES = ["Signup", "Profile", "First Use", "Paid Upgrade"]
STAGE_RANK = {stage: i for i, stage in enumerate(STAGES)}
REQUIRED_COLUMNS = ["user_id", "event_timestamp", "funnel_stage"]

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
INK_MUTED = "#8a8984"
GRID = "#e3e2de"
BLUE_RAMP = ["#184f95", "#256abf", "#3987e5", "#86b6ef"]
WARM = "#eb6834"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Funnel drop-off analysis.")
    ap.add_argument("--input", default="data/raw/subscription_logs.csv")
    ap.add_argument("--outdir", default="data/clean")
    ap.add_argument("--chart", default="reports/funnel_chart.png")
    ap.add_argument("--min-stage-users", type=int, default=1,
                    help="Fail if the first stage has fewer users than this.")
    return ap.parse_args()


def load_logs(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    try:
        logs = pd.read_csv(path, dtype={"user_id": "int64", "funnel_stage": "string"})
    except pd.errors.EmptyDataError as exc:
        raise ValueError(f"Input file is empty: {path}") from exc
    except pd.errors.ParserError as exc:
        raise ValueError(f"Malformed CSV at {path}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"Unexpected column types in {path}: {exc}") from exc

    missing = [c for c in REQUIRED_COLUMNS if c not in logs.columns]
    if missing:
        raise ValueError(f"Input file is missing required column(s): {missing}")

    logs["event_timestamp"] = pd.to_datetime(logs["event_timestamp"], errors="coerce")
    bad_ts = int(logs["event_timestamp"].isna().sum())
    if bad_ts:
        LOGGER.warning("Dropping %s row(s) with an unparseable event_timestamp", f"{bad_ts:,}")
        logs = logs.dropna(subset=["event_timestamp"])

    unknown = set(logs["funnel_stage"].dropna().unique()) - set(STAGES)
    if unknown:
        LOGGER.warning("Dropping %s row(s) with unrecognised funnel_stage: %s",
                       f"{int(logs['funnel_stage'].isin(unknown).sum()):,}", sorted(unknown))
        logs = logs[~logs["funnel_stage"].isin(unknown)]

    if logs.empty:
        raise ValueError("No usable events remain after validation.")
    return logs


def first_touch_per_stage(logs: pd.DataFrame) -> pd.DataFrame:
    """Chronological per user, de-duplicated to the first time each stage was reached."""
    before = len(logs)
    logs = logs.sort_values(["user_id", "event_timestamp"], kind="mergesort")
    logs = logs.drop_duplicates(subset=["user_id", "funnel_stage"], keep="first")
    LOGGER.info("Collapsed %s repeat emits -> %s first-touch events",
                f"{before - len(logs):,}", f"{len(logs):,}")

    wide = (logs.pivot_table(index="user_id", columns="funnel_stage",
                             values="event_timestamp", aggfunc="first"))
    for stage in STAGES:
        if stage not in wide.columns:
            wide[stage] = pd.NaT
    wide = wide[STAGES]

    # A stage only counts if every stage before it was also reached: a user who
    # appears at 'Paid Upgrade' with no 'First Use' is a logging artefact, not a
    # conversion, and silently inflates every rate downstream.
    valid = wide[STAGES[0]].notna()
    anomalies = 0
    for stage in STAGES[1:]:
        reached = wide[stage].notna()
        broken = reached & ~valid
        anomalies += int(broken.sum())
        wide.loc[broken, stage] = pd.NaT
        valid = valid & wide[stage].notna()
    if anomalies:
        LOGGER.warning("Voided %s out-of-sequence stage event(s)", f"{anomalies:,}")
    return wide


def funnel_metrics(wide: pd.DataFrame) -> pd.DataFrame:
    rows = []
    signups = int(wide["Signup"].notna().sum())
    previous = None
    for order, stage in enumerate(STAGES):
        reached = int(wide[stage].notna().sum())
        if previous is None:
            absolute, relative, step = 0, 0.0, 100.0
            days_prev = 0.0
        else:
            absolute = previous - reached
            relative = 100.0 * absolute / previous if previous else 0.0
            step = 100.0 * reached / previous if previous else 0.0
            delta = (wide[stage] - wide[STAGES[order - 1]]).dropna()
            days_prev = round(float(delta.dt.total_seconds().median() / 86400), 2)
        delta_signup = (wide[stage] - wide["Signup"]).dropna()
        rows.append({
            "stage_order": order + 1,
            "funnel_stage": stage,
            "users_reached": reached,
            "pct_of_signups": round(100.0 * reached / signups, 2) if signups else 0.0,
            "step_conversion_pct": round(step, 2),
            "absolute_dropoff": absolute,
            "relative_dropoff_pct": round(relative, 2),
            "median_days_from_previous": days_prev,
            "median_days_from_signup": round(
                float(delta_signup.dt.total_seconds().median() / 86400), 2) if len(delta_signup) else 0.0,
        })
        previous = reached
    return pd.DataFrame(rows)


def plot_funnel(metrics: pd.DataFrame, path: Path) -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "font.family": "DejaVu Sans", "text.color": INK, "axes.edgecolor": GRID,
        "xtick.color": INK_2, "ytick.color": INK_2,
    })
    fig, ax = plt.subplots(figsize=(10, 5.8))
    fig.subplots_adjust(left=0.17, right=0.95, top=0.82, bottom=0.10)

    order = metrics.iloc[::-1]
    bars = ax.barh(order["funnel_stage"], order["users_reached"], height=0.6,
                   color=list(reversed(BLUE_RAMP))[:len(order)])
    top = metrics["users_reached"].max()
    for bar, (_, row) in zip(bars, order.iterrows()):
        ax.text(bar.get_width() + top * 0.012, bar.get_y() + bar.get_height() / 2,
                f"{row['users_reached']:,}   ({row['pct_of_signups']:.1f}% of signups)",
                va="center", fontsize=10, color=INK_2)
        if row["stage_order"] > 1:
            ax.text(top * 0.012, bar.get_y() + bar.get_height() / 2,
                    f"↓ {row['relative_dropoff_pct']:.0f}% drop-off "
                    f"({row['absolute_dropoff']:,} users)",
                    va="center", fontsize=9.5, color="#ffffff", fontweight="bold")

    ax.set_xlim(0, top * 1.32)
    ax.xaxis.set_visible(False)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0, labelsize=11)
    ax.set_title("Acquisition funnel", loc="left", fontweight="bold", fontsize=13, pad=20)
    ax.text(0, 1.035, f"Signup → Paid Upgrade  ·  overall conversion "
                      f"{metrics.iloc[-1]['pct_of_signups']:.1f}%",
            transform=ax.transAxes, fontsize=9.5, color=INK_MUTED, va="bottom")

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=200)
    plt.close(fig)
    LOGGER.info("Wrote %s", path)


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s",
                        datefmt="%H:%M:%S")
    args = parse_args()
    root = Path(__file__).parent
    try:
        logs = load_logs(root / args.input)
        wide = first_touch_per_stage(logs)
        if len(wide) < args.min_stage_users:
            raise ValueError(f"Only {len(wide)} users in the funnel - refusing to report rates.")

        metrics = funnel_metrics(wide)
        outdir = (root / args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        metrics.to_csv(outdir / "funnel_metrics.csv", index=False)
        wide.to_csv(outdir / "funnel_user_stages.csv")
        plot_funnel(metrics, root / args.chart)

        LOGGER.info("Funnel:")
        for _, row in metrics.iterrows():
            LOGGER.info("  %-13s %7s users | step %5.1f%% | drop %-6s (%4.1f%%) | +%.1f days",
                        row["funnel_stage"], f"{row['users_reached']:,}",
                        row["step_conversion_pct"], f"{row['absolute_dropoff']:,}",
                        row["relative_dropoff_pct"], row["median_days_from_previous"])
        LOGGER.info("Wrote %s and %s", outdir / "funnel_metrics.csv",
                    outdir / "funnel_user_stages.csv")
    except (FileNotFoundError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 1
    except OSError as exc:
        LOGGER.error("I/O failure: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
