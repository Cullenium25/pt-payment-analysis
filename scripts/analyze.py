#!/usr/bin/env python3
"""
PT payment & market analysis: core questions answered in SQL against
data/pt_market.db, with charts written to figures/.

Questions:
  Q1  What does the US physical therapy service line look like?      (scale)
  Q2  How much does Medicare pay per PT code, nationally?             (rate card)
  Q3  How much does the payment rate vary by state, and is that
      variation a price story or a volume story?                      (the finding)
  Q4  Where is utilization concentrated, and where are the gaps?      (market map)

Usage:
    python scripts/analyze.py
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "pt_market.db"
FIG = ROOT / "figures"
OUT = ROOT / "outputs"

# style="white" keeps the background clean and draws no gridlines behind the bars.
sns.set_theme(style="white", context="talk")
PALETTE = "viridis"


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------
Q_SCALE = """
SELECT
    COUNT(DISTINCT HCPCS_Cd)          AS pt_codes,
    SUM(Tot_Rndrng_Prvdrs)            AS provider_rows,
    SUM(Tot_Benes)                    AS beneficiary_rows,
    ROUND(SUM(Tot_Srvcs), 0)          AS total_services,
    ROUND(SUM(Tot_Srvcs * Avg_Mdcr_Pymt_Amt), 0) AS est_total_medicare_payment
FROM pt_geo_2024
WHERE Rndrng_Prvdr_Geo_Lvl = 'National'
  AND Place_Of_Srvc = 'O';
"""

Q_RATE_CARD = """
SELECT
    HCPCS_Cd                            AS code,
    service_line                        AS service,
    HCPCS_Desc                          AS description,
    ROUND(SUM(Tot_Srvcs), 0)            AS total_services,
    ROUND(AVG(Avg_Sbmtd_Chrg), 2)       AS avg_submitted_charge,
    ROUND(AVG(Avg_Mdcr_Alowd_Amt), 2)   AS avg_allowed_amount,
    ROUND(AVG(Avg_Mdcr_Pymt_Amt), 2)    AS avg_medicare_payment,
    ROUND(100.0 * AVG(Avg_Mdcr_Pymt_Amt)
                / AVG(Avg_Sbmtd_Chrg), 1) AS pct_of_charge_paid
FROM pt_geo_2024
WHERE Rndrng_Prvdr_Geo_Lvl = 'National'
  AND Place_Of_Srvc = 'O'
GROUP BY HCPCS_Cd, service_line, HCPCS_Desc
ORDER BY total_services DESC;
"""

# Payment index: each state's allowed amount as a % of the national average
# allowed amount for the *same* code. Controlling for code is what makes
# cross-state comparison valid.
Q_PAYMENT_INDEX = """
WITH national_rate AS (
    SELECT HCPCS_Cd,
           AVG(Avg_Mdcr_Alowd_Amt) AS national_allowed,
           AVG(Avg_Mdcr_Stdzd_Amt) AS national_standardized
    FROM v_state_office
    GROUP BY HCPCS_Cd
)
SELECT
    s.Rndrng_Prvdr_Geo_Desc                          AS state,
    s.HCPCS_Cd                                       AS code,
    s.service_line                                   AS service,
    s.HCPCS_Desc                                     AS description,
    ROUND(s.Avg_Sbmtd_Chrg, 2)                       AS submitted_charge,
    ROUND(s.Avg_Mdcr_Alowd_Amt, 2)                   AS allowed_amount,
    ROUND(n.national_allowed, 2)                     AS national_allowed,
    ROUND(100.0 * s.Avg_Mdcr_Alowd_Amt
                / n.national_allowed, 1)             AS payment_index,
    ROUND(100.0 * s.Avg_Mdcr_Stdzd_Amt
                / n.national_standardized, 1)        AS standardized_index,
    ROUND(s.Tot_Srvcs, 0)                            AS services
FROM v_state_office s
JOIN national_rate n ON s.HCPCS_Cd = n.HCPCS_Cd
ORDER BY s.HCPCS_Cd, payment_index DESC;
"""

# Per-code spread: is the geographic variation a price story (allowed amount
# varies) or is it noise in what providers bill (charge varies)?
Q_SPREAD_BY_CODE = """
WITH per_code AS (
    SELECT HCPCS_Cd,
           service_line,
           HCPCS_Desc,
           MAX(Avg_Sbmtd_Chrg)      AS max_charge,
           MIN(Avg_Sbmtd_Chrg)      AS min_charge,
           MAX(Avg_Mdcr_Alowd_Amt)  AS max_allowed,
           MIN(Avg_Mdcr_Alowd_Amt)  AS min_allowed,
           MAX(Avg_Mdcr_Stdzd_Amt)  AS max_stdzd,
           MIN(Avg_Mdcr_Stdzd_Amt)  AS min_stdzd,
           SUM(Tot_Srvcs)           AS services
    FROM v_state_office
    GROUP BY HCPCS_Cd, service_line, HCPCS_Desc
)
SELECT HCPCS_Cd AS code,
       service_line AS service,
       substr(HCPCS_Desc, 1, 46) AS description,
       ROUND(max_charge  / min_charge,  2) AS charge_ratio,
       ROUND(max_allowed / min_allowed, 2) AS allowed_ratio,
       ROUND(max_stdzd   / min_stdzd,   2) AS standardized_ratio,
       ROUND(services, 0)                  AS services
FROM per_code
WHERE services > 100000
ORDER BY services DESC;
"""

# Where is PT utilization highest per beneficiary? (volume story)
Q_UTILIZATION = """
SELECT
    Rndrng_Prvdr_Geo_Desc                    AS state,
    ROUND(SUM(Tot_Srvcs), 0)                 AS services,
    SUM(Tot_Rndrng_Prvdrs)                   AS provider_rows,
    ROUND(SUM(Tot_Srvcs) / SUM(Tot_Rndrng_Prvdrs), 0) AS services_per_provider,
    ROUND(SUM(Tot_Benes) / SUM(Tot_Rndrng_Prvdrs), 1) AS benes_per_provider
FROM v_state_office
GROUP BY Rndrng_Prvdr_Geo_Desc
ORDER BY services DESC;
"""


def q(db: sqlite3.Connection, sql: str) -> pd.DataFrame:
    return pd.read_sql_query(sql, db)


def strip_spines(ax) -> None:
    """Drop the axes border. The bars carry the chart; the box adds nothing."""
    for spine in ax.spines.values():
        spine.set_visible(False)


def strip_xaxis(ax) -> None:
    """Drop the x scale. Each bar carries its own value label instead."""
    ax.set_xticks([])
    ax.set_xlabel("")
    ax.tick_params(axis="x", length=0)


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------
def fig_rate_card(rate: pd.DataFrame) -> None:
    top = rate.head(12).iloc[::-1]
    fig, ax = plt.subplots(figsize=(13, 8))
    y = range(len(top))
    ax.barh(y, top.avg_allowed_amount, color=sns.color_palette(PALETTE, len(top)),
            label="Medicare allowed amount")
    ax.barh(y, top.avg_medicare_payment, color="#444", height=0.35,
            label="Medicare paid")
    ax.set_yticks(list(y))
    # "97110 - Therapeutic exercise" reads better than a bare code or the full
    # CMS sentence, which runs to three wrapped lines on this chart.
    ax.set_yticklabels([f"{r.code} - {r.service}" for r in top.itertuples()],
                       fontsize=9)
    for i, r in enumerate(top.itertuples()):
        ax.text(r.avg_allowed_amount + 1.5, i, f"${r.avg_allowed_amount:,.2f}",
                va="center", fontsize=8, weight="bold")
        # The paid amount goes inside its own bar so it cannot collide with the
        # allowed label sitting at the end of the longer bar.
        ax.text(r.avg_medicare_payment - 1.5, i, f"${r.avg_medicare_payment:,.2f}",
                va="center", ha="right", fontsize=7, color="white")
    strip_xaxis(ax)
    ax.set_title("Medicare PT rate card: national average, office setting (2024)\n"
                 "Top 12 codes by volume", fontsize=13, weight="bold")
    ax.legend(loc="lower right", fontsize=9, frameon=False)
    strip_spines(ax)
    fig.tight_layout()
    fig.savefig(FIG / "01_rate_card.png", dpi=150)
    plt.close(fig)


def fig_payment_index(idx: pd.DataFrame, code: str = "97110") -> None:
    d = idx[idx.code == code].sort_values("payment_index")
    fig, ax = plt.subplots(figsize=(10, 14))
    colors = sns.color_palette("RdYlGn", len(d))
    ax.barh(range(len(d)), d.payment_index, color=colors)
    ax.axvline(100, color="black", ls="--", lw=1.5, label="National average = 100")
    ax.set_yticks(range(len(d)))
    ax.set_yticklabels(d.state, fontsize=8)
    for i, r in enumerate(d.itertuples()):
        ax.text(r.payment_index + 0.6, i, f"{r.payment_index:.0f}",
                va="center", fontsize=7)
    strip_xaxis(ax)
    ax.set_title(f"Medicare payment index by state: {code} - {d.service.iloc[0]}",
                 fontsize=12, weight="bold")
    ax.legend(loc="lower left", frameon=False)
    strip_spines(ax)
    fig.tight_layout()
    fig.savefig(FIG / f"02_payment_index_{code}.png", dpi=150)
    plt.close(fig)


def fig_spread(spread: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(12, 7))
    d = spread.sort_values("charge_ratio")
    y = range(len(d))
    ax.barh(y, d.charge_ratio, color="#C44E52", label="Billed charge (max ÷ min state)")
    ax.barh(y, d.allowed_ratio, color="#4C72B0", label="Medicare allowed (max ÷ min)")
    ax.barh(y, d.standardized_ratio, color="#55A868", label="Standardized (max ÷ min)")
    ax.set_yticks(list(y))
    ax.set_yticklabels([f"{r.code} - {r.service}" for r in d.itertuples()], fontsize=9)
    ax.axvline(1, color="black", lw=1)
    ax.set_xlabel("Ratio across states (higher = more geographic variation)")
    ax.set_title("What actually varies across states?\n"
                 "Billing is highly variable; actual payment is nearly uniform",
                 fontsize=13, weight="bold")
    ax.legend(fontsize=9, frameon=False)
    strip_spines(ax)
    fig.tight_layout()
    fig.savefig(FIG / "03_variation_source.png", dpi=150)
    plt.close(fig)


def fig_state_map(util: pd.DataFrame) -> None:
    d = util.sort_values("services", ascending=False).head(20).iloc[::-1]
    fig, ax = plt.subplots(figsize=(11, 9))
    ax.barh(range(len(d)), d.services, color=sns.color_palette(PALETTE, len(d)))
    ax.set_yticks(range(len(d)))
    ax.set_yticklabels(d.state, fontsize=10)
    for i, r in enumerate(d.itertuples()):
        ax.text(r.services * 1.01, i, f"{r.services/1e6:.1f}M", va="center", fontsize=8)
    ax.set_title("PT service volume by state, top 20 (all 21 codes, office setting, 2024)\n"
                 "Volume concentration, not payment rate, drives revenue",
                 fontsize=13, weight="bold")
    strip_spines(ax)
    strip_xaxis(ax)
    fig.tight_layout()
    fig.savefig(FIG / "04_volume_by_state.png", dpi=150)
    plt.close(fig)


def main() -> int:
    if not DB.exists():
        print(f"ERROR: {DB} not found. Run: python scripts/fetch_data.py && "
              f"python scripts/build_db.py", file=sys.stderr)
        return 1

    FIG.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    db = sqlite3.connect(DB)

    scale = q(db, Q_SCALE)
    rate = q(db, Q_RATE_CARD)
    idx = q(db, Q_PAYMENT_INDEX)
    spread = q(db, Q_SPREAD_BY_CODE)
    util = q(db, Q_UTILIZATION)

    for name, df in [("scale", scale), ("rate_card", rate), ("payment_index", idx),
                     ("spread_by_code", spread), ("utilization", util)]:
        df.to_csv(OUT / f"{name}.csv", index=False)

    pd.set_option("display.width", 200)
    print("\n=== Q1. US physical therapy service line, Medicare FFS, 2024 (office) ===")
    s = scale.iloc[0]
    print(f"  PT codes analysed        : {s.pt_codes:.0f}")
    print(f"  Total services           : {s.total_services:,.0f}")
    print(f"  Est. total Medicare paid : ${s.est_total_medicare_payment:,.0f}")
    print("  NOTE: provider and beneficiary counts are summed across codes and "
          "therefore double-count; they are not unique counts.")

    print("\n=== Q2. National PT rate card (top 8 by volume) ===")
    print(rate.head(8)[["code", "service", "total_services",
                        "avg_submitted_charge", "avg_allowed_amount",
                        "pct_of_charge_paid"]].to_string(index=False))

    print("\n=== Q3. Geographic variation: price vs. billing noise ===")
    print(spread[["code", "service", "charge_ratio", "allowed_ratio",
                  "standardized_ratio"]].to_string(index=False))
    print(f"\n  MEDIAN across codes:")
    print(f"    billed charge varies {spread.charge_ratio.median():.2f}x across states")
    print(f"    Medicare allowed    {spread.allowed_ratio.median():.2f}x")
    print(f"    standardized amount {spread.standardized_ratio.median():.2f}x")

    print("\n=== Q4. Volume concentration (top 10 states) ===")
    print(util.head(10)[["state", "services", "provider_rows",
                         "services_per_provider"]].to_string(index=False))

    fig_rate_card(rate)
    fig_payment_index(idx)
    fig_spread(spread)
    fig_state_map(util)
    print(f"\n[ok  ] 4 figures -> {FIG}/")
    print(f"[ok  ] 5 CSVs    -> {OUT}/")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
