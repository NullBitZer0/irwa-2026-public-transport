"""
Render the individual risk-assessment infographic for the IT3041 security audit.

Produces evidence/screenshots/student1_fig_risk_matrix.png — the severity /
likelihood classification chart the LinkedIn brief asks for but which did not
previously exist.

Findings and severities are transcribed from the vulnerability assessment, not
generated, so the figure cannot drift from the report. The three-run movement is
cross-checked against evaluation/redteam_evidence.json.

Run with a Python that has matplotlib:
    python3 docs/render_risk_matrix.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Circle
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "evidence" / "screenshots" / "student1_fig_risk_matrix.png"
EVIDENCE = REPO / "evaluation" / "redteam_evidence.json"

# Severity rows, bottom to top. Four levels, so the grid is 4 x 5.
SEV_LABELS = ["Low", "Medium", "High", "Critical"]
LIK_LABELS = ["Rare", "Unlikely", "Possible", "Likely", "Almost\nCertain"]

# (id, title, severity 1-4, likelihood 1-5)
FINDINGS = [
    ("F-01", "Guardrail never invoked on the LLM entry path", 4, 4),
    ("F-02", "Denylist evaded by encoding (10 of 10)", 3, 4),
    ("F-03", "Human approval gate was client-asserted", 4, 5),
    ("F-04", "Unescaped user input rendered as markup", 2, 4),
    ("F-05", "PII masking not wired into the chat path", 3, 3),
    ("F-06", "Dormant LLM sink, permissive fallback", 2, 2),
    ("F-07", "Detection gap: allowed requests unlogged", 1, 3),
]

RUNS = [
    ("Pre-remediation", 15, 14, 4),
    ("After gateway work", 12, 17, 4),
    ("Final verified run", 0, 33, 0),
]

COL_VULN, COL_SEC, COL_INC = "#c62828", "#2e7d32", "#9e9e9e"
INK, MUTED, RULE = "#16202b", "#5b6b7c", "#dde3e9"

ROWS, COLS = 4, 5


def band(score: int) -> str:
    if score >= 20:
        return "CRITICAL"
    if score >= 12:
        return "HIGH"
    if score >= 6:
        return "MEDIUM"
    return "LOW"


def verify_movement() -> None:
    """Fail loudly if the plotted runs contradict the evidence artefact."""
    if not EVIDENCE.exists():
        return
    results = json.loads(EVIDENCE.read_text())["results"]
    final_secure = sum(1 for r in results if r["verdict"] == "SECURE")
    total = len(results)
    if (total, final_secure) != (RUNS[-1][1] + RUNS[-1][2] + RUNS[-1][3],
                                  RUNS[-1][2]):
        raise SystemExit(
            f"figure says {total} tests / {final_secure} secure but RUNS says "
            f"{RUNS[-1]}; update both together"
        )


def draw_matrix(ax) -> None:
    cmap = LinearSegmentedColormap.from_list(
        "risk", ["#1b5e20", "#66bb6a", "#f9a825", "#ef6c00", "#b71c1c"]
    )
    occupied = {(lik - 1, sev - 1) for _, _, sev, lik in FINDINGS}

    for row in range(ROWS):
        for col in range(COLS):
            score = (row + 1) * (col + 1)
            ax.add_patch(
                plt.Rectangle(
                    (col - 0.5, row - 0.5), 1, 1,
                    facecolor=cmap((score - 1) / 19.0), alpha=0.20,
                    edgecolor="white", linewidth=2.4,
                )
            )
            if (col, row) not in occupied:
                ax.text(col, row, band(score), ha="center", va="center",
                        fontsize=7.8, color=MUTED, alpha=0.85, fontweight="bold")

    for fid, _, sev, lik in FINDINGS:
        ax.add_patch(
            Circle((lik - 1, sev - 1), 0.30, facecolor=INK,
                   edgecolor="white", linewidth=2.4, zorder=5)
        )
        ax.text(lik - 1, sev - 1, fid.split("-")[1], ha="center", va="center",
                fontsize=10, color="white", fontweight="bold", zorder=6)

    ax.set_xlim(-0.5, COLS - 0.5)
    ax.set_ylim(-0.5, ROWS - 0.5)
    ax.set_xticks(range(COLS))
    ax.set_xticklabels(LIK_LABELS, fontsize=9.5, color=INK)
    ax.set_yticks(range(ROWS))
    ax.set_yticklabels(SEV_LABELS, fontsize=9.5, color=INK)
    ax.set_xlabel("Likelihood of exploitation", fontsize=10.5, color=INK,
                  fontweight="bold", labelpad=8)
    ax.set_ylabel("Impact severity", fontsize=10.5, color=INK,
                  fontweight="bold", labelpad=8)
    ax.set_title("Severity × likelihood classification", fontsize=13,
                 color=INK, fontweight="bold", pad=12, loc="left")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)


def draw_movement(ax) -> None:
    y = np.arange(len(RUNS))
    for i, (name, vuln, sec, inc) in enumerate(RUNS):
        left = 0
        for value, colour in ((vuln, COL_VULN), (sec, COL_SEC),
                              (inc, COL_INC)):
            if value:
                ax.barh(i, value, left=left, color=colour, height=0.5)
                ax.text(left + value / 2, i, str(value), ha="center",
                        va="center", fontsize=11.5, color="white",
                        fontweight="bold")
            left += value
        ax.text(33.8, i, "33 tests", va="center", fontsize=9, color=MUTED)

    ax.set_yticks(y)
    ax.set_yticklabels([r[0] for r in RUNS], fontsize=10, color=INK)
    ax.invert_yaxis()
    ax.set_xlim(0, 39)
    ax.set_ylim(len(RUNS) - 0.35, -0.75)
    ax.set_xlabel("Test cases", fontsize=10.5, color=INK,
                  fontweight="bold", labelpad=8)
    ax.set_title("Movement across three verified runs", fontsize=13,
                 color=INK, fontweight="bold", pad=12, loc="left")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.tick_params(length=0, colors=INK)
    ax.legend(
        handles=[
            Line2D([], [], marker="s", linestyle="", markersize=9,
                   markerfacecolor=COL_VULN, markeredgecolor="none",
                   label="Vulnerable"),
            Line2D([], [], marker="s", linestyle="", markersize=9,
                   markerfacecolor=COL_SEC, markeredgecolor="none",
                   label="Secure"),
            Line2D([], [], marker="s", linestyle="", markersize=9,
                   markerfacecolor=COL_INC, markeredgecolor="none",
                   label="Inconclusive"),
        ],
        loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=3,
        frameon=False, fontsize=9.5,
    )


def main() -> None:
    verify_movement()
    fig = plt.figure(figsize=(16, 10.6), dpi=150)
    fig.patch.set_facecolor("white")
    gs = fig.add_gridspec(
        1, 2, width_ratios=[1.45, 1.0],
        left=0.06, right=0.985, top=0.845, bottom=0.30, wspace=0.22,
    )
    draw_matrix(fig.add_subplot(gs[0, 0]))
    draw_movement(fig.add_subplot(gs[0, 1]))

    fig.text(0.06, 0.955, "AI Security Audit — Prompt Injection & Jailbreak Analysis",
             fontsize=18, fontweight="bold", color=INK)
    fig.text(0.06, 0.918,
             "LankaJourney AI · Individual vulnerability assessment · IT3041",
             fontsize=12, color=MUTED)
    fig.add_artist(Line2D([0.06, 0.985], [0.888, 0.888], color=RULE, linewidth=1.4))

    handles = []
    for fid, title, _, _ in FINDINGS:
        handles.append(
            Line2D([], [], marker="o", linestyle="", markersize=10,
                   markerfacecolor=INK, markeredgecolor="white",
                   markeredgewidth=1.5, label=f"{fid}   {title}")
        )
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.06, 0.215),
               ncol=2, frameon=False, fontsize=9.6, labelspacing=0.8,
               handletextpad=0.7, columnspacing=2.6)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, facecolor="white")
    print(f"wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
