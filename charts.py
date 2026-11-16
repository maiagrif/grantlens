"""Altair charts for the dashboard.

Color rules: funders are always blue (first) and orange (second); a two-period comparison uses aqua and violet so
it never reads as a funder. Both pairs pass the colorblind-separation checks. Every chart has tooltips, and the
pages show the underlying table beside it.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

FUNDER_COLORS = ["#2a78d6", "#eb6834"]
PERIOD_COLORS = ["#1baf7a", "#6250d6"]
SEQUENTIAL = ["#cde2fb", "#86b6ef", "#3987e5", "#256abf", "#184f95"]


def _axis(**kw):
    return alt.Axis(labelLimit=260, grid=False, domain=False, ticks=False, **kw)


def grouped_share_bars(df: pd.DataFrame, category: str, series: str, colors: list[str], title: str) -> alt.Chart:
    """Horizontal bars: share of each series' dollars per category, series side by side.

    df columns: category, series, share (0-1), usd, grants.
    """
    order = (df.groupby(category)["share"].sum().sort_values(ascending=False).index.tolist())
    series_order = list(dict.fromkeys(df[series]))
    base = alt.Chart(df, title=title).encode(
        y=alt.Y(f"{category}:N", sort=order, title=None, axis=_axis()),
        yOffset=alt.YOffset(f"{series}:N", sort=series_order),
        color=alt.Color(f"{series}:N", sort=series_order, title=None,
                        scale=alt.Scale(domain=series_order, range=colors[: len(series_order)]),
                        legend=alt.Legend(orient="top", direction="horizontal")),
        tooltip=[alt.Tooltip(f"{series}:N", title=series.title()), alt.Tooltip(f"{category}:N", title=category.title()),
                 alt.Tooltip("share:Q", title="Share of dollars", format=".1%"),
                 alt.Tooltip("usd:Q", title="Dollars", format="$,.0f"), alt.Tooltip("grants:Q", title="Grants", format=",")],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height={"band": 0.85}, stroke="white", strokeWidth=1).encode(
        x=alt.X("share:Q", title="Share of each funder's dollars", axis=alt.Axis(format="%", grid=True, gridOpacity=0.3,
                                                                                domain=False, ticks=False)))
    labels = base.mark_text(align="left", dx=4, fontSize=11, color="#6b6a64").encode(
        x="share:Q", text=alt.Text("share:Q", format=".0%"))
    height = max(220, 30 * len(order) * max(1, len(series_order)) // 1.4)
    return (bars + labels).properties(height=int(height))


def trend_small_multiples(df: pd.DataFrame, colors: list[str], title: str) -> alt.Chart:
    """One small line chart per theme: each funder's share of dollars by year.

    df columns: theme, funder, start_year, share, usd, grants.
    """
    funders = list(dict.fromkeys(df["funder"]))
    order = df.groupby("theme")["share"].mean().sort_values(ascending=False).index.tolist()
    base = alt.Chart(df).encode(
        x=alt.X("start_year:O", title=None, axis=alt.Axis(labelAngle=0, values=list(range(2006, 2031, 5)),
                                                           domain=False, ticks=False)),
        y=alt.Y("share:Q", title=None, axis=alt.Axis(format="%", tickCount=3, grid=True, gridOpacity=0.3,
                                                     domain=False, ticks=False)),
        color=alt.Color("funder:N", sort=funders, title=None,
                        scale=alt.Scale(domain=funders, range=colors[: len(funders)]),
                        legend=alt.Legend(orient="top", direction="horizontal")),
        tooltip=[alt.Tooltip("funder:N", title="Funder"), alt.Tooltip("theme:N", title="Theme"),
                 alt.Tooltip("start_year:O", title="Year"), alt.Tooltip("share:Q", title="Share of dollars", format=".1%"),
                 alt.Tooltip("usd:Q", title="Dollars", format="$,.0f"), alt.Tooltip("grants:Q", title="Grants")],
    )
    hover = base.mark_circle(size=60, opacity=0.001).encode(  # invisible, wide hover targets
        color=alt.Color("funder:N", sort=funders, scale=alt.Scale(domain=funders, range=colors[: len(funders)]),
                        legend=None))
    lines = base.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=14, filled=True)) + hover
    return (
        lines.properties(width=150, height=100)
        .facet(facet=alt.Facet("theme:N", sort=order, title=None, header=alt.Header(labelFontWeight="bold",
                                                                                     labelAnchor="start")),
               columns=2, title=title)
        .resolve_scale(y="independent")
    )


def confusion_heatmap(df: pd.DataFrame, title: str, reference_title: str = "Reference label") -> alt.Chart:
    """df columns: reference, AI, grants. Diagonal = agreement."""
    cats = sorted(set(df["reference"]) | set(df["AI"]))
    base = alt.Chart(df, title=title).encode(
        x=alt.X("AI:N", sort=cats, title="AI label", axis=alt.Axis(labelAngle=-40, labelLimit=180)),
        y=alt.Y("reference:N", sort=cats, title=reference_title, axis=alt.Axis(labelLimit=180)),
        tooltip=[alt.Tooltip("reference:N", title=reference_title), "AI:N", "grants:Q"],
    )
    cells = base.mark_rect(stroke="white", strokeWidth=2, cornerRadius=3).encode(
        color=alt.Color("grants:Q", scale=alt.Scale(range=SEQUENTIAL), legend=alt.Legend(title="Grants")))
    text = base.mark_text(fontSize=11).encode(
        text="grants:Q",
        color=alt.condition(alt.datum.grants >= df["grants"].max() * 0.6, alt.value("white"), alt.value("#1a1a19")))
    return (cells + text).properties(height=max(240, 26 * len(cats)))
