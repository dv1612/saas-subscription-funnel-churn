"""
generate_dashboard.py
---------------------
Renders the SaaS funnel & churn dashboard to high-resolution PNGs and a
two-page PDF, so the analysis is reviewable without Power BI.

Inputs : data/clean/funnel_metrics.csv, cohort_retention.csv,
         mrr_movement.csv, subscriptions_enriched.csv
Outputs: reports/*.png, reports/saas_dashboard.pdf

Usage:
    python generate_dashboard.py
"""

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LinearSegmentedColormap, to_rgb
from matplotlib.patches import FancyBboxPatch

SURFACE = "#fcfcfb"
TILE_BG = "#f2f1ee"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#8a8984"
GRID = "#e3e2de"
SERIES_1 = "#2a78d6"     # blue  - growth / retained
SERIES_2 = "#eb6834"     # orange - churn / loss
NEUTRAL = "#c9c8c3"
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SEQ_CMAP = LinearSegmentedColormap.from_list("seq_blue", BLUE_RAMP)
DPI = 200
CCY = "£"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": "DejaVu Sans", "text.color": INK_PRIMARY,
    "axes.labelcolor": INK_SECONDARY, "xtick.color": INK_SECONDARY,
    "ytick.color": INK_SECONDARY, "axes.edgecolor": GRID, "axes.linewidth": 0.8,
    "xtick.labelsize": 9, "ytick.labelsize": 9, "axes.titlesize": 12,
})


def money(v):
    if abs(v) >= 1_000_000:
        return f"{CCY}{v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{CCY}{v / 1_000:.0f}K"
    return f"{CCY}{v:,.0f}"


def strip_axes(ax, keep=("left", "bottom")):
    for side in ("top", "right", "left", "bottom"):
        if side not in keep:
            ax.spines[side].set_visible(False)
    ax.tick_params(length=0)


def panel_title(ax, title, subtitle=None):
    ax.set_title(title, loc="left", color=INK_PRIMARY, fontweight="bold",
                 pad=16 if subtitle else 8)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9,
                color=INK_MUTED, va="bottom")


def ink_on(color):
    r, g, b = to_rgb(color)
    return INK_PRIMARY if (0.299 * r + 0.587 * g + 0.114 * b) > 0.6 else "#ffffff"


def kpi_row(fig, gs, kpis):
    for idx, (label, value, note) in enumerate(kpis):
        ax = fig.add_subplot(gs[0, idx])
        ax.set_axis_off()
        ax.add_patch(FancyBboxPatch((0.01, 0.06), 0.98, 0.88, transform=ax.transAxes,
                                    boxstyle="round,pad=0.01,rounding_size=0.04",
                                    facecolor=TILE_BG, edgecolor="none", zorder=0))
        ax.text(0.06, 0.70, label.upper(), transform=ax.transAxes, fontsize=9.5,
                color=INK_SECONDARY)
        ax.text(0.06, 0.34, value, transform=ax.transAxes, fontsize=27,
                color=INK_PRIMARY, fontweight="bold")
        ax.text(0.06, 0.16, note, transform=ax.transAxes, fontsize=9, color=INK_MUTED)


# --- panels ------------------------------------------------------------------
def funnel(ax, metrics):
    order = metrics.iloc[::-1]
    shades = [SEQ_CMAP(0.30 + 0.60 * (r / 100) ** 0.5) for r in order["pct_of_signups"]]
    bars = ax.barh(order["funnel_stage"], order["users_reached"], height=0.6, color=shades)
    top = metrics["users_reached"].max()
    for bar, (_, row) in zip(bars, order.iterrows()):
        ax.text(bar.get_width() + top * 0.015, bar.get_y() + bar.get_height() / 2,
                f"{row['users_reached']:,}  ({row['pct_of_signups']:.0f}%)",
                va="center", fontsize=9.5, color=INK_SECONDARY)
        if row["stage_order"] > 1:
            ax.text(top * 0.015, bar.get_y() + bar.get_height() / 2,
                    f"↓ {row['relative_dropoff_pct']:.0f}% lost "
                    f"· {row['median_days_from_previous']:.1f}d to convert",
                    va="center", fontsize=8.8, color=ink_on(bar.get_facecolor()),
                    fontweight="bold")
    ax.set_xlim(0, top * 1.30)
    ax.xaxis.set_visible(False)
    strip_axes(ax, keep=())
    ax.tick_params(labelsize=10)
    panel_title(ax, "Acquisition funnel",
                f"Signup → Paid Upgrade  ·  "
                f"{metrics.iloc[-1]['pct_of_signups']:.1f}% overall conversion")


def churn_by_plan(ax, subs):
    data = (subs.groupby("plan")
                .agg(customers=("user_id", "size"), churned=("is_churned", "sum"),
                     active_mrr=("active_mrr", "sum")))
    data["churn_pct"] = 100 * data["churned"] / data["customers"]
    data = data.sort_values("churn_pct")
    bars = ax.barh(data.index, data["churn_pct"], height=0.55,
                   color=[SERIES_2 if v == data["churn_pct"].max() else NEUTRAL
                          for v in data["churn_pct"]])
    for bar, (name, row) in zip(bars, data.iterrows()):
        ax.text(bar.get_width() + 1.0, bar.get_y() + bar.get_height() / 2,
                f"{row['churn_pct']:.1f}%   ·   {money(row['active_mrr'])} active MRR",
                va="center", fontsize=9.5, color=INK_SECONDARY)
    ax.set_xlim(0, data["churn_pct"].max() * 1.75)
    ax.xaxis.set_visible(False)
    strip_axes(ax, keep=())
    ax.tick_params(labelsize=10)
    panel_title(ax, "Lifetime churn by plan",
                "Share of each plan's customers who have cancelled")


def mrr_movement(ax, mrr):
    months = pd.to_datetime(mrr["month"])
    x = np.arange(len(months))
    ax.bar(x - 0.21, mrr["new_mrr"], width=0.42, color=SERIES_1, label="New MRR")
    ax.bar(x + 0.21, -mrr["lost_mrr"], width=0.42, color=SERIES_2, label="Churned MRR")
    ax.plot(x, mrr["net_new_mrr"], color=INK_PRIMARY, linewidth=1.8, marker="o",
            markersize=4, markerfacecolor=SURFACE, markeredgewidth=1.6, label="Net new MRR")
    ax.axhline(0, color=INK_SECONDARY, linewidth=0.9)
    ax.set_xticks(x[::2], [m.strftime("%b %y") for m in months[::2]])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: money(v)))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    strip_axes(ax, keep=("bottom",))
    leg = ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=3,
                    labelcolor=INK_SECONDARY)
    panel_title(ax, "MRR movement by month",
                "New vs churned MRR, same scale  ·  black line is the net")


def active_mrr_trend(ax, mrr):
    months = pd.to_datetime(mrr["month"])
    ax.plot(months, mrr["active_mrr"], color=SERIES_1, linewidth=2.2)
    ax.fill_between(months, mrr["active_mrr"], color=SERIES_1, alpha=0.10)
    last = mrr.iloc[-1]
    ax.annotate(f"{money(last['active_mrr'])}", xy=(months.iloc[-1], last["active_mrr"]),
                xytext=(-4, 10), textcoords="offset points", ha="right",
                fontsize=10.5, fontweight="bold", color=INK_PRIMARY)
    ax.set_ylim(0, mrr["active_mrr"].max() * 1.22)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: money(v)))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    strip_axes(ax, keep=("bottom",))
    panel_title(ax, "Active MRR", "Month-end recurring revenue on the book")


def cohort_matrix(ax, cohort, max_month=9):
    pivot = (cohort.pivot(index="cohort_month", columns="month_number",
                          values="retention_pct")
                   .sort_index())
    pivot = pivot[[c for c in pivot.columns if c <= max_month]]
    sizes = cohort.groupby("cohort_month")["cohort_users"].max().sort_index()

    data = pivot.to_numpy(dtype=float)
    ax.imshow(np.where(np.isnan(data), np.nan, data / 100.0), cmap=SEQ_CMAP,
              vmin=0, vmax=1, aspect="auto")
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            if np.isnan(data[i, j]):
                continue
            shade = SEQ_CMAP(data[i, j] / 100.0)
            ax.text(j, i, f"{data[i, j]:.0f}", ha="center", va="center",
                    fontsize=7.6, color=ink_on(shade),
                    fontweight="bold" if j == 0 else "normal")
    labels = [f"{pd.Timestamp(m):%b %y}  (n={sizes[m]:,})" for m in pivot.index]
    ax.set_yticks(range(len(labels)), labels, fontsize=8)
    ax.set_xticks(range(data.shape[1]), [f"M{c}" for c in pivot.columns], fontsize=8.5)
    ax.set_xlabel("Months since signup")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)
    panel_title(ax, "Cohort retention matrix (%)",
                "Each row is a signup cohort  ·  darker = more of the cohort still paying")


def retention_curve(ax, cohort):
    # Only cohorts old enough to have reached month N belong in month N's
    # denominator - pooling every cohort would count young ones as lost.
    grouped = cohort.groupby("month_number")[["retained_users", "cohort_users"]].sum()
    pct = (100 * grouped["retained_users"] / grouped["cohort_users"])
    pct = pct[pct.index <= 12]
    ax.plot(pct.index, pct.values, color=SERIES_1, linewidth=2.2, marker="o",
            markersize=5, markerfacecolor=SURFACE, markeredgewidth=2)
    for m in (1, 3, 6, 12):
        if m in pct.index:
            ax.annotate(f"M{m}: {pct[m]:.0f}%", xy=(m, pct[m]), xytext=(6, 8),
                        textcoords="offset points", fontsize=9, color=INK_SECONDARY)
    ax.set_ylim(0, 105)
    ax.set_xlabel("Months since signup")
    ax.set_ylabel("Users still paying (%)")
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    strip_axes(ax, keep=("bottom", "left"))
    panel_title(ax, "Retention curve",
                "Weighted across the cohorts that have reached each month")


def churn_by_tenure(ax, subs):
    bins = [-1, 0, 3, 6, 12, 999]
    labels = ["Month 0", "1-3", "4-6", "7-12", "12+"]
    churned = subs[subs["is_churned"] == 1].copy()
    churned["bucket"] = pd.cut(churned["tenure_months"], bins=bins, labels=labels)
    data = churned.groupby("bucket", observed=False).agg(
        users=("user_id", "size"), mrr=("lost_mrr", "sum"))
    share = 100 * data["users"] / data["users"].sum()
    bars = ax.bar(data.index.astype(str), data["users"], width=0.6,
                  color=[SERIES_2 if v == share.max() else NEUTRAL for v in share])
    for bar, (idx, row) in zip(bars, data.iterrows()):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + data["users"].max() * 0.03,
                f"{int(row['users']):,}\n{money(row['mrr'])}", ha="center",
                fontsize=9, color=INK_SECONDARY)
    ax.set_ylim(0, data["users"].max() * 1.28)
    ax.set_xlabel("Months survived before cancelling")
    ax.yaxis.set_visible(False)
    strip_axes(ax, keep=("bottom",))
    panel_title(ax, "When customers cancel",
                "Churned customers by tenure, with the MRR they took with them")


# --- pages -------------------------------------------------------------------
def page_one(funnel_m, subs, mrr, kpis):
    fig = plt.figure(figsize=(16, 9))
    gs = fig.add_gridspec(3, 4, height_ratios=[0.60, 1.30, 1.15],
                          left=0.085, right=0.965, top=0.825, bottom=0.075,
                          hspace=0.62, wspace=0.34)
    fig.text(0.085, 0.945, "SaaS Subscription Funnel & Churn  ·  Executive Dashboard",
             fontsize=21, fontweight="bold", color=INK_PRIMARY)
    fig.text(0.085, 0.905,
             f"{len(subs):,} paying customers  ·  "
             f"{pd.to_datetime(mrr['month']).min():%b %Y} - "
             f"{pd.to_datetime(mrr['month']).max():%b %Y}  ·  mock dataset",
             fontsize=10.5, color=INK_SECONDARY)
    fig.text(0.965, 0.915, "Page 1 of 2  ·  Funnel & revenue", fontsize=9.5,
             color=INK_MUTED, ha="right")
    kpi_row(fig, gs, kpis)
    funnel(fig.add_subplot(gs[1, :2]), funnel_m)
    churn_by_plan(fig.add_subplot(gs[1, 2:]), subs)
    mrr_movement(fig.add_subplot(gs[2, :]), mrr)
    return fig


def page_two(cohort, subs, mrr):
    fig = plt.figure(figsize=(16, 9))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.45, 1.0],
                          left=0.115, right=0.965, top=0.815, bottom=0.075,
                          hspace=0.52, wspace=0.26)
    fig.text(0.115, 0.945, "Retention Detail  ·  Cohorts, Survival and Timing",
             fontsize=21, fontweight="bold", color=INK_PRIMARY)
    fig.text(0.115, 0.905,
             "Cohort = the month a customer started paying  ·  "
             "a customer counts as retained in every month before they cancel",
             fontsize=10.5, color=INK_SECONDARY)
    fig.text(0.965, 0.915, "Page 2 of 2  ·  Retention", fontsize=9.5,
             color=INK_MUTED, ha="right")
    cohort_matrix(fig.add_subplot(gs[0, :]), cohort)
    retention_curve(fig.add_subplot(gs[1, 0]), cohort)
    churn_by_tenure(fig.add_subplot(gs[1, 1]), subs)
    return fig


def save_panel(func, args, path, figsize=(9, 5.6), left=0.16, bottom=0.14):
    fig, ax = plt.subplots(figsize=figsize)
    fig.subplots_adjust(left=left, right=0.96, top=0.84, bottom=bottom)
    func(ax, *args)
    fig.savefig(path, dpi=DPI)
    plt.close(fig)


def main() -> int:
    root = Path(__file__).parent
    clean, reports = root / "data/clean", root / "reports"
    reports.mkdir(exist_ok=True)
    try:
        funnel_m = pd.read_csv(clean / "funnel_metrics.csv")
        cohort = pd.read_csv(clean / "cohort_retention.csv")
        mrr = pd.read_csv(clean / "mrr_movement.csv")
        subs = pd.read_csv(clean / "subscriptions_enriched.csv")
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}\nRun funnel_analysis.py and the cohort SQL first.",
              file=sys.stderr)
        return 1

    last = mrr.iloc[-1]
    kpis = [
        ("Active MRR", money(last["active_mrr"]), f"{int(last['active_customers']):,} customers"),
        ("Monthly MRR churn", f"{last['mrr_churn_pct']:.1f}%",
         f"{money(last['lost_mrr'])} lost in {pd.Timestamp(last['month']):%b %Y}"),
        ("Signup → paid", f"{funnel_m.iloc[-1]['pct_of_signups']:.1f}%",
         f"{funnel_m.iloc[-1]['users_reached']:,} of {funnel_m.iloc[0]['users_reached']:,}"),
        ("Lifetime churn", f"{100 * subs['is_churned'].mean():.0f}%",
         f"{money(subs['lost_mrr'].sum())} of MRR lost to date"),
    ]

    p1, p2 = page_one(funnel_m, subs, mrr, kpis), page_two(cohort, subs, mrr)
    p1.savefig(reports / "dashboard_page1_funnel.png", dpi=DPI)
    p2.savefig(reports / "dashboard_page2_retention.png", dpi=DPI)
    with PdfPages(reports / "saas_dashboard.pdf") as pdf:
        pdf.savefig(p1)
        pdf.savefig(p2)
        pdf.infodict()["Title"] = "SaaS Subscription Funnel & Churn Dashboard"
    plt.close(p1)
    plt.close(p2)

    save_panel(funnel, (funnel_m,), reports / "01_funnel.png", left=0.17)
    save_panel(churn_by_plan, (subs,), reports / "02_churn_by_plan.png", left=0.13)
    save_panel(mrr_movement, (mrr,), reports / "03_mrr_movement.png", figsize=(11, 5.4))
    save_panel(cohort_matrix, (cohort,), reports / "04_cohort_matrix.png",
               figsize=(11, 6.4), left=0.22)
    save_panel(retention_curve, (cohort,), reports / "05_retention_curve.png")
    save_panel(churn_by_tenure, (subs,), reports / "06_churn_by_tenure.png")
    save_panel(active_mrr_trend, (mrr,), reports / "07_active_mrr.png", figsize=(11, 5))

    for item in sorted(reports.iterdir()):
        print(f"  {item.relative_to(root)}  ({item.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
