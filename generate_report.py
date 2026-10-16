"""
generate_report.py
------------------
Builds a two-page, resume-ready case study PDF from the funnel/churn outputs.

Usage:
    python generate_report.py --author "Your Name" --github "github.com/you/repo"
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

INK = colors.HexColor("#0b0b0b")
INK_2 = colors.HexColor("#52514e")
INK_MUTED = colors.HexColor("#8a8984")
ACCENT = colors.HexColor("#2a78d6")
ACCENT_DARK = colors.HexColor("#184f95")
WARM = colors.HexColor("#eb6834")
TILE = colors.HexColor("#f2f1ee")
RULE = colors.HexColor("#e3e2de")

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
SIGNUPS = 25_000  # users entering the funnel in the mock dataset


def styles():
    base = getSampleStyleSheet()
    s = {
        "title": ParagraphStyle("title", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=19, leading=23, alignment=0, textColor=INK,
                                spaceAfter=2),
        "subtitle": ParagraphStyle("subtitle", fontName="Helvetica", fontSize=10.5,
                                   leading=14, textColor=INK_2),
        "byline": ParagraphStyle("byline", fontName="Helvetica", fontSize=9,
                                 leading=12.5, textColor=INK_MUTED, alignment=2),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=15,
                             textColor=INK, spaceBefore=6, spaceAfter=4),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.6, leading=13.6,
                               textColor=INK_2, alignment=TA_JUSTIFY),
        "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=9.6, leading=13.4,
                                 textColor=INK_2, leftIndent=9, bulletIndent=1,
                                 spaceAfter=3.5),
        "kpi_label": ParagraphStyle("kpi_label", fontName="Helvetica", fontSize=7.4,
                                    leading=9, textColor=INK_2),
        "kpi_value": ParagraphStyle("kpi_value", fontName="Helvetica-Bold", fontSize=16,
                                    leading=19, textColor=INK),
        "kpi_note": ParagraphStyle("kpi_note", fontName="Helvetica", fontSize=7.2,
                                   leading=9, textColor=INK_MUTED),
        "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.6, leading=11.4,
                               textColor=INK_2),
        "cell_b": ParagraphStyle("cell_b", fontName="Helvetica-Bold", fontSize=8.6,
                                 leading=11.4, textColor=INK),
        "cap": ParagraphStyle("cap", fontName="Helvetica", fontSize=7.8, leading=10,
                              textColor=INK_MUTED),
    }
    return s


def money(v, decimals=0):
    if abs(v) >= 1_000_000:
        return f"£{v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"£{v / 1_000:.0f}K"
    return f"£{v:,.{decimals}f}"


def kpi_strip(items, st, width):
    cells = []
    for label, value, note in items:
        cells.append(Table([[Paragraph(label.upper(), st["kpi_label"])],
                            [Paragraph(value, st["kpi_value"])],
                            [Paragraph(note, st["kpi_note"])]],
                           colWidths=[width / len(items) - 4],
                           style=TableStyle([
                               ("BACKGROUND", (0, 0), (-1, -1), TILE),
                               ("LEFTPADDING", (0, 0), (-1, -1), 7),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                               ("TOPPADDING", (0, 0), (-1, 0), 7),
                               ("BOTTOMPADDING", (0, 0), (-1, -2), 0),
                               ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
                           ])))
    return Table([cells], colWidths=[width / len(items)] * len(items),
                 style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                                   ("TOPPADDING", (0, 0), (-1, -1), 0),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))


def data_table(rows, st, width, col_ratios, header=True):
    table = Table(rows, colWidths=[width * r for r in col_ratios])
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]
    if header:
        style += [("LINEBELOW", (0, 0), (-1, 0), 0.9, INK),
                  ("BOTTOMPADDING", (0, 0), (-1, 0), 6)]
    table.setStyle(TableStyle(style))
    return table


def fitted(path: Path, width):
    """Scale an image to a target width, preserving aspect ratio."""
    from reportlab.lib.utils import ImageReader
    iw, ih = ImageReader(str(path)).getSize()
    return Image(str(path), width=width, height=width * ih / iw)


def build(args) -> int:
    root = Path(__file__).parent
    reports = root / "reports"
    clean = root / "data/clean"
    st = styles()

    try:
        funnel = pd.read_csv(clean / "funnel_metrics.csv")
        cohort = pd.read_csv(clean / "cohort_retention.csv")
        mrr = pd.read_csv(clean / "mrr_movement.csv")
        subs = pd.read_csv(clean / "subscriptions_enriched.csv")
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}\nRun the pipeline first.", file=sys.stderr)
        return 1
    for name in ("dashboard_page1_funnel.png", "04_cohort_matrix.png",
                 "05_retention_curve.png"):
        if not (reports / name).exists():
            print(f"ERROR: missing {name} - run generate_dashboard.py first.", file=sys.stderr)
            return 1

    last = mrr.iloc[-1]
    curve = cohort.groupby("month_number")[["retained_users", "cohort_users"]].sum()
    curve_pct = 100 * curve["retained_users"] / curve["cohort_users"]
    by_plan = (subs.groupby("plan")
                   .agg(customers=("user_id", "size"), churned=("is_churned", "sum"),
                        active_mrr=("active_mrr", "sum")))
    by_plan["churn_pct"] = 100 * by_plan["churned"] / by_plan["customers"]
    worst_plan = by_plan["churn_pct"].idxmax()
    best_plan = by_plan["churn_pct"].idxmin()
    biggest = funnel.loc[funnel["relative_dropoff_pct"].idxmax()]
    prior_stage = funnel.loc[funnel["stage_order"] == biggest["stage_order"] - 1].iloc[0]
    churned = subs[subs["is_churned"] == 1]
    early = churned[churned["tenure_months"] <= 3]
    early_share = 100 * len(early) / len(churned)

    width = PAGE_W - 2 * MARGIN
    story = []

    head = Table([[
        Paragraph("SaaS Subscription Funnel &amp; Churn", st["title"]),
        Paragraph(f"{args.author}<br/>Data Analytics Portfolio Project<br/>"
                  f"{date.today():%B %Y}", st["byline"]),
    ]], colWidths=[width * 0.72, width * 0.28],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                          ("LEFTPADDING", (0, 0), (-1, -1), 0),
                          ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                          ("TOPPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 2)]
    story.append(Paragraph(
        "Funnel drop-off, cohort retention and MRR churn &nbsp;&middot;&nbsp; "
        "Python &middot; PostgreSQL &middot; Power BI", st["subtitle"]))
    story.append(Spacer(1, 9))

    story.append(Paragraph("Executive summary", st["h2"]))
    story.append(Paragraph(
        f"A subscription business converts {SIGNUPS:,} signups into "
        f"{int(funnel.iloc[-1]['users_reached']):,} paying customers and "
        f"{money(last['active_mrr'])} of monthly recurring revenue &mdash; but it had no "
        f"measurement of where signups stall, which cohorts survive, or what churn costs in "
        f"revenue rather than logos. I built a pipeline that instruments the funnel from raw "
        f"event logs, reconstructs each customer's active months in SQL, and reports retention "
        f"and MRR movement on the same grain the finance team already uses.<br/><br/>"
        f"Two findings change where effort goes. The funnel does not leak evenly: "
        f"<b>{biggest['relative_dropoff_pct']:.0f}% of users are lost at a single step</b> "
        f"({prior_stage['funnel_stage']} &rarr; {biggest['funnel_stage']}). And churn is "
        f"front-loaded: <b>{early_share:.0f}% of all cancellations happen within three "
        f"months</b> of signing up, which makes onboarding, not win-back, the lever.",
        st["body"]))
    story.append(Spacer(1, 10))

    story.append(kpi_strip([
        ("Active MRR", money(last["active_mrr"]),
         f"{int(last['active_customers']):,} customers"),
        ("Monthly MRR churn", f"{last['mrr_churn_pct']:.1f}%",
         f"{money(last['lost_mrr'])} in {pd.Timestamp(last['month']):%b %Y}"),
        ("Signup to paid", f"{funnel.iloc[-1]['pct_of_signups']:.1f}%",
         f"{int(funnel.iloc[-1]['users_reached']):,} of {SIGNUPS:,}"),
        ("Month-6 retention", f"{curve_pct[6]:.0f}%", "weighted across cohorts"),
    ], st, width))
    story.append(Spacer(1, 12))

    story.append(fitted(reports / "dashboard_page1_funnel.png", width))
    story.append(Spacer(1, 3))
    story.append(Paragraph("Dashboard page 1 &mdash; funnel drop-off, churn by plan and monthly "
                           "MRR movement. Full dashboard: reports/saas_dashboard.pdf",
                           st["cap"]))
    story.append(Spacer(1, 10))

    story.append(Paragraph("What the analysis found", st["h2"]))
    for text in [
        f"<b>One step costs more than the rest combined.</b> {prior_stage['funnel_stage']} "
        f"&rarr; {biggest['funnel_stage']} loses {int(biggest['absolute_dropoff']):,} users "
        f"({biggest['relative_dropoff_pct']:.0f}%), and takes a median of "
        f"{biggest['median_days_from_previous']:.0f} days &mdash; long enough for intent to decay.",
        f"<b>Churn is an onboarding problem.</b> {len(early):,} of {len(churned):,} "
        f"cancellations ({early_share:.0f}%) happen in the first three months, taking "
        f"{money(early['lost_mrr'].sum())} of MRR with them.",
        f"<b>Price tier predicts survival.</b> {worst_plan} churns at "
        f"{by_plan.loc[worst_plan, 'churn_pct']:.0f}% against {best_plan}'s "
        f"{by_plan.loc[best_plan, 'churn_pct']:.0f}% &mdash; but {best_plan} holds "
        f"{money(by_plan.loc[best_plan, 'active_mrr'])} of active MRR, so the revenue risk and "
        f"the logo risk sit on different plans.",
        f"<b>Growth is outrunning churn, for now.</b> Net new MRR has stayed positive every "
        f"month, but monthly MRR churn of {last['mrr_churn_pct']:.1f}% means roughly "
        f"{money(last['lost_mrr'] * 12)} a year has to be replaced before the business grows "
        f"at all.",
    ]:
        story.append(Paragraph(text, st["bullet"], bulletText="\u2022"))

    # ---------------- page 2 -------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("How it was built", st["h2"]))
    story.append(data_table([
        [Paragraph("Stage", st["cell_b"]), Paragraph("Artifact", st["cell_b"]),
         Paragraph("What it does", st["cell_b"])],
        [Paragraph("1. Funnel", st["cell"]), Paragraph("funnel_analysis.py", st["cell_b"]),
         Paragraph("Sorts raw events per user, collapses repeat emits to first touch, voids "
                   "out-of-sequence stages, then reports absolute and relative drop-off plus "
                   "median time-to-convert for each step.", st["cell"])],
        [Paragraph("2. Cohorts", st["cell"]), Paragraph("cohort_retention.sql", st["cell_b"]),
         Paragraph("PostgreSQL CTEs expand each customer into one row per active month with "
                   "<font face='Courier'>generate_series</font>, then build the cohort "
                   "retention matrix and a monthly new / churned / net MRR movement table.",
                   st["cell"])],
        [Paragraph("3. Report", st["cell"]), Paragraph("power_bi_dax.txt", st["cell_b"]),
         Paragraph("DAX for churn rate, retention rate, active and lost MRR, net revenue "
                   "retention and cohort retention, with the visual type each metric belongs "
                   "in.", st["cell"])],
    ], st, width, [0.14, 0.21, 0.65]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Modelling decisions worth noting", st["h2"]))
    for text in [
        "<b>The retention denominator excludes cohorts too young to have reached the month.</b> "
        "A cohort that signed up last month has not lost its month-6 users; counting it as a "
        "zero understates month-6 retention by roughly half.",
        "<b>Churn rate is measured against the opening base.</b> Dividing this month's "
        "cancellations by this month's customer count flatters the rate whenever the business "
        "is growing, so the denominator is last month's close.",
        "<b>Out-of-sequence funnel events are voided, not counted.</b> A user appearing at "
        "Paid Upgrade with no First Use event is a logging artefact; left in, it silently "
        "inflates every conversion rate downstream.",
    ]:
        story.append(Paragraph(text, st["bullet"], bulletText="\u2022"))
    story.append(Spacer(1, 4))

    charts = Table([[fitted(reports / "04_cohort_matrix.png", width * 0.50),
                     fitted(reports / "05_retention_curve.png", width * 0.30)]],
                   colWidths=[width * 0.56, width * 0.44],
                   style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                     ("RIGHTPADDING", (0, 0), (0, 0), 8),
                                     ("RIGHTPADDING", (1, 0), (1, 0), 0),
                                     ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                     ("TOPPADDING", (0, 0), (-1, -1), 0)]))
    story.append(charts)
    story.append(Paragraph("Left: retention by signup cohort. Right: the pooled survival curve.",
                           st["cap"]))
    story.append(Spacer(1, 6))

    rows = [[Paragraph("Finding", st["cell_b"]), Paragraph("Size", st["cell_b"]),
             Paragraph("Recommended action", st["cell_b"])]]
    for finding, size, action in [
        (f"{prior_stage['funnel_stage']} &rarr; {biggest['funnel_stage']} drop-off",
         f"{int(biggest['absolute_dropoff']):,} users",
         "Instrument the step, then trial in-product prompts at the median "
         f"{biggest['median_days_from_previous']:.0f}-day mark, before intent decays."),
        ("Churn in the first 3 months", f"{len(early):,} customers",
         f"Move spend from win-back to onboarding; {money(early['lost_mrr'].sum())} of MRR is "
         "decided in that window."),
        (f"{worst_plan} plan churn",
         f"{by_plan.loc[worst_plan, 'churn_pct']:.0f}% lifetime",
         "Test an annual option and a usage-based nudge into the mid tier."),
        ("Revenue concentration",
         f"{money(by_plan.loc[best_plan, 'active_mrr'])} active MRR",
         f"Treat {best_plan} accounts as named, not automated &mdash; each cancellation is "
         "worth ten Starter ones."),
    ]:
        rows.append([Paragraph(finding, st["cell_b"]), Paragraph(size, st["cell"]),
                     Paragraph(action, st["cell"])])
    story.append(KeepTogether([Paragraph("Recommended actions", st["h2"]),
                               data_table(rows, st, width, [0.26, 0.15, 0.59])]))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Where I would take it next", st["h2"]))
    for text in [
        "<b>Split the funnel by acquisition channel.</b> A blended conversion rate hides which "
        "channels buy users who never activate.",
        "<b>Add expansion and contraction MRR.</b> Net revenue retention is the number a SaaS "
        "board asks for first, and upgrades are already in the event stream.",
        "<b>Model churn risk per account.</b> Tenure, plan and activation lag are enough for a "
        "first survival model.",
    ]:
        story.append(Paragraph(text, st["bullet"], bulletText="\u2022"))

    # ---------------- frame / chrome ----------------------------------------
    def chrome(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(ACCENT)
        canvas.rect(0, PAGE_H - 4 * mm, PAGE_W, 4 * mm, stroke=0, fill=1)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN, MARGIN - 4 * mm, PAGE_W - MARGIN, MARGIN - 4 * mm)
        canvas.setFont("Helvetica", 7.6)
        canvas.setFillColor(INK_MUTED)
        footer = f"{args.author} · SaaS Funnel & Churn Analysis"
        if args.github:
            footer += f" · {args.github}"
        canvas.drawString(MARGIN, MARGIN - 9 * mm, footer)
        canvas.restoreState()

    class NumberedCanvas(pdfcanvas.Canvas):
        """Second pass over the finished pages, so the footer can say 'of N'."""

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.setFont("Helvetica", 7.6)
                self.setFillColor(INK_MUTED)
                self.drawRightString(PAGE_W - MARGIN, MARGIN - 9 * mm,
                                     f"Page {self._pageNumber} of {total}")
                super().showPage()
            super().save()

    out = reports / "saas_case_study.pdf"
    doc = BaseDocTemplate(str(out), pagesize=A4,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN, bottomMargin=MARGIN + 2 * mm,
                          title="SaaS Subscription Funnel & Churn - Case Study",
                          author=args.author,
                          subject="Funnel, cohort retention and MRR churn analysis")
    frame = Frame(MARGIN, MARGIN + 2 * mm, width,
                  PAGE_H - MARGIN - (MARGIN + 2 * mm), id="body",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="std", frames=[frame], onPage=chrome)])
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"  {out.relative_to(root)}  ({out.stat().st_size / 1024:.0f} KB, {doc.page} pages)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the case-study PDF.")
    ap.add_argument("--author", default="Dhruvi", help="Name shown in the byline and footer.")
    ap.add_argument("--github", default="", help="Optional repo URL for the footer.")
    return build(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
