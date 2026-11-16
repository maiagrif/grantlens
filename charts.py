"""Altair charts for the dashboard.

Color rules: funders are always blue (first) and orange (second); a two-period comparison uses aqua and violet so
it never reads as a funder. Both pairs pass the colorblind-separation checks. Every chart has tooltips, and the
pages show the underlying table beside it. Phone-friendly: titles and legends are page text (they wrap), long axis
labels break onto two lines, and small multiples are laid out with st.columns (which stack on narrow screens).
"""

from __future__ import annotations

import altair as alt
import pandas as pd

FUNDER_COLORS = ["#2a78d6", "#eb6834"]
PERIOD_COLORS = ["#1baf7a", "#6250d6"]
SEQUENTIAL = ["#cde2fb", "#86b6ef", "#3987e5", "#256abf", "#184f95"]


def _axis(**kw):
    return alt.Axis(labelLimit=200, grid=False, domain=False, ticks=False, **kw)


def wrap(label: str, width: int = 18) -> str:
    """Break a long category name onto two lines ('|' marks the break) so charts fit a phone screen."""
    if len(label) <= width:
        return label
    words, best = label.split(" "), None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if best is None or max(len(a), len(b)) < max(map(len, best)):
            best = (a, b)
    return "|".join(best) if best else label


MULTILINE = "split(datum.label, '|')"


def legend_html(names: list[str], colors: list[str]) -> str:
    """A legend as page text, so it wraps on narrow screens instead of running off the chart."""
    items = "".join(f'<span style="white-space:nowrap;margin-right:16px"><span style="color:{c}">●</span> {n}</span>'
                    for n, c in zip(names, colors))
    return f'<div style="font-size:14px;line-height:1.8">{items}</div>'


def grouped_share_bars(df: pd.DataFrame, category: str, series: str, colors: list[str]) -> alt.Chart:
    """Horizontal bars: share of each series' dollars per category, series side by side.

    df columns: category, series, share (0-1), usd, grants. Title and legend are shown by the page (legend_html),
    because chart-drawn ones don't wrap on phones. Long category names wrap onto two lines.
    """
    df = df.assign(_label=df[category].map(wrap))
    order = df.groupby("_label")["share"].sum().sort_values(ascending=False).index.tolist()
    series_order = list(dict.fromkeys(df[series]))
    base = alt.Chart(df).encode(
        y=alt.Y("_label:N", sort=order, title=None, axis=_axis(labelExpr=MULTILINE, labelLineHeight=13)),
        yOffset=alt.YOffset(f"{series}:N", sort=series_order),
        color=alt.Color(f"{series}:N", sort=series_order, title=None,
                        scale=alt.Scale(domain=series_order, range=colors[: len(series_order)]), legend=None),
        tooltip=[alt.Tooltip(f"{series}:N", title=series.title()), alt.Tooltip(f"{category}:N", title=category.title()),
                 alt.Tooltip("share:Q", title="Share of dollars", format=".1%"),
                 alt.Tooltip("usd:Q", title="Dollars", format="$,.0f"), alt.Tooltip("grants:Q", title="Grants", format=",")],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height={"band": 0.85}, stroke="white", strokeWidth=1).encode(
        x=alt.X("share:Q", title="Share of dollars", axis=alt.Axis(format="%", grid=True, gridOpacity=0.3,
                                                                   domain=False, ticks=False, tickCount=4)))
    labels = base.mark_text(align="left", dx=4, fontSize=11, color="#6b6a64").encode(
        x="share:Q", text=alt.Text("share:Q", format=".0%"))
    height = max(220, 34 * len(order) * max(1, len(series_order)) // 1.4)
    return (bars + labels).properties(height=int(height))


def trend_line(df: pd.DataFrame, funders: list[str], colors: list[str], y_max: float) -> alt.Chart:
    """One small line chart for one theme: each funder's share of dollars by year. Pages lay these out in
    st.columns, which stack into one column on phones.

    df columns: funder, start_year, share, usd, grants.
    """
    base = alt.Chart(df).encode(
        x=alt.X("start_year:Q", title=None, scale=alt.Scale(domain=[2006, 2026]),
                axis=alt.Axis(format="d", values=[2010, 2015, 2020, 2025], domain=False, ticks=False)),
        y=alt.Y("share:Q", title=None, scale=alt.Scale(domain=[0, y_max]),
                axis=alt.Axis(format="%", tickCount=3, grid=True, gridOpacity=0.3, domain=False, ticks=False,
                              labelFlush=False)),
        color=alt.Color("funder:N", sort=funders, scale=alt.Scale(domain=funders, range=colors[: len(funders)]),
                        legend=None),
        tooltip=[alt.Tooltip("funder:N", title="Funder"), alt.Tooltip("start_year:Q", title="Year", format="d"),
                 alt.Tooltip("share:Q", title="Share of dollars", format=".1%"),
                 alt.Tooltip("usd:Q", title="Dollars", format="$,.0f"), alt.Tooltip("grants:Q", title="Grants")],
    )
    hover = base.mark_circle(size=80, opacity=0.001)  # invisible, wide hover targets
    return (base.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=14, filled=True)) + hover).properties(height=110)


def confusion_heatmap(df: pd.DataFrame, reference_title: str = "Reference label") -> alt.Chart:
    """df columns: reference, AI, grants. Diagonal = agreement. Fixed cell size so numbers stay legible;
    on a phone the chart scrolls sideways inside its own box (see app.py CSS) rather than shrinking."""
    df = df.assign(_ref=df["reference"].map(wrap), _ai=df["AI"].map(wrap))
    cats = sorted(set(df["_ref"]) | set(df["_ai"]))
    base = alt.Chart(df).encode(
        x=alt.X("_ai:N", sort=cats, title="AI label", axis=alt.Axis(
            orient="top", labelAngle=-45, labelAlign="left", labelExpr="replace(datum.label, '|', ' ')",
            labelOverlap=False, labelLimit=200, domain=False, ticks=False)),
        y=alt.Y("_ref:N", sort=cats, title=reference_title, axis=alt.Axis(
            labelExpr=MULTILINE, labelOverlap=False, labelLimit=160, domain=False, ticks=False)),
        tooltip=[alt.Tooltip("reference:N", title=reference_title), alt.Tooltip("AI:N", title="AI label"), "grants:Q"],
    )
    cells = base.mark_rect(stroke="white", strokeWidth=2, cornerRadius=3).encode(
        color=alt.Color("grants:Q", scale=alt.Scale(range=SEQUENTIAL), legend=alt.Legend(title="Grants")))
    text = base.mark_text(fontSize=11).encode(
        text="grants:Q",
        color=alt.condition(alt.datum.grants >= df["grants"].max() * 0.6, alt.value("white"), alt.value("#1a1a19")))
    return (cells + text).properties(width=36 * len(cats) + 170, height=40 * len(cats) + 160)  # + room for axis labels
