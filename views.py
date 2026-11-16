"""GrantLens pages. app.py wires them into the navigation; tests run each one alone."""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

import benchmark
import charts
import db
import metrics
import taxonomy

READONLY = os.environ.get("GRANTLENS_READONLY", "").lower() in ("1", "true", "yes")
MAX_QUESTIONS = int(os.environ.get("MAX_QUESTIONS_PER_SESSION", "15"))
COMPARE_FROM = 2015  # first year both funders have labels
LAST_YEAR = 2026


@st.cache_resource
def con():
    return db.connect()


def sql(query: str, params: list | None = None) -> pd.DataFrame:
    return pd.read_sql_query(query, con(), params=params or [])


def money(x: float) -> str:
    return f"${x / 1e9:.1f}B" if x >= 1e9 else f"${x / 1e6:.0f}M"


def with_shares(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    df["share"] = df["usd"] / df.groupby(by)["usd"].transform("sum")
    return df


def show_table(df: pd.DataFrame, label: str = "Show the data") -> None:
    with st.expander(label):
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"share": st.column_config.NumberColumn("share", format="percent"),
                                    "usd": st.column_config.NumberColumn("dollars", format="dollar")})


def labels_missing() -> bool:
    if sql("SELECT COUNT(*) n FROM grants_labeled WHERE theme IS NOT NULL")["n"][0] == 0:
        st.info("No AI labels yet. Run `uv run python label.py submit <model>`, then `collect`, then "
                "`uv run python db.py` (see the README).")
        return True
    return False


# ------------------------------------------------------------------ pages

def page_compare():
    st.title("Compare funders")
    st.caption("Grant portfolios sorted into one shared set of themes and strategies by AI, so funders can be "
               "compared side by side. See “Check the AI” for how accurate the labels are.")
    ov = sql("SELECT * FROM (SELECT funder, COUNT(*) grants, SUM(amount_usd) usd, SUM(theme IS NOT NULL) labeled, "
             "MIN(start_year) y0, MAX(start_year) y1 FROM grants_labeled GROUP BY funder)")
    for col, r in zip(st.columns(len(ov)), ov.itertuples()):
        col.metric(r.funder, f"{r.grants:,} grants")
        col.caption(f"{money(r.usd)} · {r.y0}–{r.y1} · {r.labeled:,} labeled")
    if labels_missing():
        return
    src = db.primary_model(con())
    if src == "keyword-baseline":
        st.warning("These labels come from the simple keyword baseline, not Claude. Run the AI labeling "
                   "(README) to replace them.")
    else:
        st.caption(f"Labels by {src}.")

    y0, y1 = st.slider("Years", 2006, LAST_YEAR, (COMPARE_FROM, LAST_YEAR),
                       help="Gates labels start in 2015, so comparisons are fairest from 2015 on.")
    for field, title in (("theme", "Where each funder's dollars go, by theme"),
                         ("strategy", "How each funder gives, by strategy"),
                         ("population", "Who each funder's grants are meant to reach")):
        df = sql(f"SELECT funder, {field}, SUM(amount_usd) usd, COUNT(*) grants FROM grants_labeled "
                 f"WHERE {field} IS NOT NULL AND start_year BETWEEN ? AND ? GROUP BY funder, {field}", [y0, y1])
        if df.empty:
            continue
        df = with_shares(df, ["funder"])
        st.altair_chart(charts.grouped_share_bars(df, field, "funder", charts.FUNDER_COLORS, f"{title}, {y0}–{y1}"),
                        width="stretch")
        show_table(df.sort_values(["funder", "share"], ascending=[True, False]))

    st.subheader("How each theme's share changed over time")
    df = sql("SELECT funder, theme, start_year, SUM(amount_usd) usd, COUNT(*) grants FROM grants_labeled "
             "WHERE theme IS NOT NULL AND start_year BETWEEN 2006 AND ? GROUP BY funder, theme, start_year", [LAST_YEAR])
    st.caption("Mott from 2006 (earlier years have too few grants to show a share); Gates from 2015.")
    df = with_shares(df, ["funder", "start_year"])
    st.altair_chart(charts.trend_small_multiples(df, charts.FUNDER_COLORS, "Share of each funder's dollars, by year"))
    show_table(df.sort_values(["theme", "funder", "start_year"]))


def page_explore():
    st.title("Explore a funder")
    funders = sql("SELECT DISTINCT funder FROM grants ORDER BY 1")["funder"].tolist()
    c1, c2, c3 = st.columns([1, 1, 1])
    funder = c1.selectbox("Funder", funders)
    place = c2.text_input("Place or program contains", placeholder="e.g. Flint, Kenya, Global Health")
    yrs = sql("SELECT MIN(start_year) a, MAX(start_year) b FROM grants WHERE funder = ? AND in_scope = 1", [funder])
    lo, hi = int(yrs["a"][0]), min(int(yrs["b"][0]), LAST_YEAR)
    split = c3.slider("Compare before / from year", lo + 1, hi, min(max(2015, lo + 1), hi),
                      help="Splits the grants into two periods to show how the mix shifted.")

    where, params = ["funder = ?", "in_scope = 1", "start_year <= ?"], [funder, LAST_YEAR]
    if place:
        where.append("(location LIKE ? OR geographic_focus LIKE ? OR program LIKE ? OR subprogram LIKE ?)")
        params += [f"%{place}%"] * 4
    w = " AND ".join(where)
    totals = sql(f"SELECT COUNT(*) n, SUM(amount_usd) usd FROM grants_labeled WHERE {w}", params)
    st.caption(f"{totals['n'][0]:,} grants · {money(totals['usd'][0] or 0)}" + (f" matching “{place}”" if place else ""))
    if totals["n"][0] == 0:
        return

    if not labels_missing():
        df = sql(f"""SELECT CASE WHEN start_year < {int(split)} THEN 'Before {int(split)}' ELSE '{int(split)} on' END period,
                     theme, SUM(amount_usd) usd, COUNT(*) grants FROM grants_labeled
                     WHERE {w} AND theme IS NOT NULL GROUP BY period, theme ORDER BY period DESC""", params)
        df = with_shares(df, ["period"])
        df = df.rename(columns={"period": "period"})
        st.altair_chart(charts.grouped_share_bars(df, "theme", "period", charts.PERIOD_COLORS,
                                                  "How the theme mix shifted (share of dollars)"),
                        width="stretch")
        show_table(df)

    st.subheader("Largest grantees")
    top = sql(f"SELECT grantee, COUNT(*) grants, SUM(amount_usd) usd, MIN(start_year) first_year, "
              f"MAX(start_year) last_year FROM grants_labeled WHERE {w} GROUP BY grantee ORDER BY usd DESC LIMIT 15",
              params)
    st.dataframe(top, hide_index=True, width="stretch",
                 column_config={"usd": st.column_config.NumberColumn("dollars", format="dollar")})

    st.subheader("Grants")
    themes = ["All themes", *taxonomy.THEMES]
    pick = st.selectbox("Theme", themes)
    gw, gp = w, list(params)
    if pick != "All themes":
        gw += " AND theme = ?"
        gp.append(pick)
    grants = sql(f"SELECT url, start_year year, grantee, title, amount_usd usd, theme, strategy, population, confidence "
                 f"FROM grants_labeled WHERE {gw} ORDER BY start_date DESC LIMIT 500", gp)
    st.dataframe(grants, hide_index=True, width="stretch",
                 column_config={"url": st.column_config.LinkColumn("grant", display_text=r"/([^/]+)/?$"),
                                "usd": st.column_config.NumberColumn("dollars", format="dollar"),
                                "year": st.column_config.NumberColumn(format="%d")})


def page_check():
    st.title("Check the AI")
    st.caption("Before trusting AI labels, measure them. No hand-labeling needed: each funder's own staff already "
               "sorted its grants into programs and topics, so those categories serve as the answer key for theme. "
               "The AI never sees them.")
    tabs = st.tabs(["Results", "Spot-check by hand (optional)"] if not READONLY else ["Results"])
    with tabs[0]:
        check_results()
    if not READONLY:
        with tabs[1]:
            hand_spot_check()


def fmt_kappa(k):
    return f"{k:.2f} ({metrics.interpret(k)})" if k is not None else ""


def check_results():
    c = con()
    st.subheader("1. Theme accuracy against the funder's own categories")
    cov = pd.DataFrame(benchmark.coverage(c))
    st.write(" · ".join(f"**{r['funder']}**: {r['with a funder theme']:,} of {r['in scope']:,} grants "
                        f"({r['share']:.0%}) have a funder category that maps to exactly one theme"
                        for r in cov.to_dict("records")))
    with st.expander("How funder categories were mapped to themes"):
        cmap = pd.read_csv(benchmark.MAP_PATH).fillna("")
        st.caption("Each category was mapped once, by hand. Categories that span themes (catch-alls, "
                   "“special initiatives”, mixed portfolios) are left blank and excluded from scoring.")
        st.dataframe(cmap, hide_index=True, width="stretch")

    c1, c2 = st.columns(2)
    scope = c1.radio("Funder", ["Both funders", "Mott Foundation", "Gates Foundation"], horizontal=True,
                     help="Gates records are one-line purpose statements, so its labels rest on less text.")
    sample_only = c2.radio("Grants", ["Validation sample (300)", "Every grant with a funder theme"],
                           horizontal=True, help="Every model labels the sample; only the chosen model labels "
                           "every grant.").startswith("Validation")
    funder = None if scope == "Both funders" else scope
    rows = benchmark.theme_accuracy(c, funder, sample_only)
    if not rows:
        st.info("No labels to score yet. Run `uv run python label.py validate haiku sonnet opus`, "
                "then `uv run python db.py`.")
        return
    table = pd.DataFrame(rows)
    table["kappa"] = table["kappa"].map(fmt_kappa)
    st.dataframe(table.drop(columns="strength"), hide_index=True, width="stretch")
    st.caption("Cohen's kappa = agreement beyond chance (1 = perfect, 0 = chance); bands follow Landis & Koch. "
               "keyword-baseline is free keyword matching: the bar the AI has to clear. Funder categories are "
               "not perfect truth either, so some “disagreements” are reasonable second opinions.")

    names = table["model"].tolist()
    prim = db.primary_model(c)
    model = st.selectbox("Look closer at", names, index=names.index(prim) if prim in names else 0)
    c1, c2 = st.columns([3, 2])
    with c1:
        cm = pd.DataFrame(benchmark.confusion(c, model, funder, sample_only))
        st.altair_chart(charts.confusion_heatmap(cm, "Theme: funder's category vs. the AI's (diagonal = agree)",
                                                 "Funder's category"), width="stretch")
    with c2:
        st.write("**Does the AI know when it's unsure?**")
        st.dataframe(pd.DataFrame(benchmark.accuracy_by_confidence(c, model, funder, sample_only)),
                     hide_index=True, width="stretch")
        st.caption("If low-confidence labels match less often, those grants can be routed to a person.")
    dis = pd.DataFrame(benchmark.disagreements(c, model, funder, sample_only))
    with st.expander(f"Where the funder and {model} disagreed ({len(dis):,} shown)"):
        if dis.empty:
            st.success("No disagreements.")
        else:
            st.dataframe(dis, hide_index=True, width="stretch",
                         column_config={"grant": st.column_config.LinkColumn(display_text=r"/([^/]+)/?$")})

    st.subheader("2. Do the models agree with each other?")
    st.caption("Funders don't record strategy or population, so there is no answer key for those. Agreement "
               "between independent models shows how stable the labels are, not whether they are right.")
    ag = pd.DataFrame(benchmark.model_agreement(c, funder))
    if ag.empty or len(benchmark.models(c)) < 2:
        st.info("Needs at least two labelers on the same grants.")
    else:
        for f in taxonomy.FIELDS:
            ag[f"{f} kappa"] = ag[f"{f} kappa"].map(fmt_kappa)
        st.dataframe(ag, hide_index=True, width="stretch")

    human = metrics.load_human()
    rows = metrics.agreement(c, funder) if human else []
    if rows:
        st.subheader("3. Hand spot-check")
        st.caption(f"{len(human)} grants labeled by hand: too few for firm numbers, but a useful sanity check.")
        t = pd.DataFrame(rows)
        for f in taxonomy.FIELDS:
            t[f"{f} kappa"] = t[f"{f} kappa"].map(fmt_kappa)
        st.dataframe(t, hide_index=True, width="stretch")


def hand_spot_check():
    ids = []
    try:
        import label  # noqa: PLC0415  (only needed locally)
        ids = label.sample_ids()
    except FileNotFoundError:
        st.info("Draw the sample first: `uv run python label.py sample`")
        return
    human = metrics.load_human()
    done = sum(g in human for g in ids)
    st.progress(done / len(ids), text=f"{done} of {len(ids)} labeled")
    st.caption("You see exactly what the AI sees, and never the AI's answer, so your labels stay independent.")

    todo = [g for g in ids if g not in human]
    choice = st.selectbox("Grant", ids, index=ids.index(todo[0]) if todo else 0,
                          format_func=lambda g: f"{'✓' if g in human else '·'} {g}")
    g = db.get(con(), [choice])[0]
    st.text(taxonomy.grant_text(g))
    prev = human.get(choice, {})

    with st.form(f"label-{choice}"):
        picks = {}
        for f, options in taxonomy.FIELDS.items():
            names = list(options)
            picks[f] = st.radio(f.title(), names, index=names.index(prev[f]) if prev.get(f) in names else None,
                                horizontal=True, captions=None, help="\n".join(f"**{k}**: {v}" for k, v in options.items()))
        note = st.text_input("Note (optional)", value=prev.get("note", ""), placeholder="Why this was a judgment call")
        if st.form_submit_button("Save and next", type="primary"):
            if any(v is None for v in picks.values()):
                st.error("Pick a value for each field.")
            else:
                metrics.save_human(choice, picks["theme"], picks["strategy"], picks["population"], note)
                st.rerun()


def page_ask():
    import agent  # noqa: PLC0415  (needs the API key in the environment)

    st.title("Ask")
    st.caption("Plain-English questions about the grants. Every answer cites the grant records it used, "
               "and every number comes from a query over the data.")
    examples = [
        "How do Mott and Gates compare on general operating support since 2015?",
        "What did Mott fund in response to the Flint water crisis, and how much?",
        "Which Gates grants support community health workers in Africa?",
        "How has Mott's Flint Area giving shifted across themes since 2010?",
    ]
    cols = st.columns(len(examples))
    for c, ex in zip(cols, examples):
        if c.button(ex, width="stretch"):
            st.session_state.pending = ex

    st.session_state.setdefault("chat", [])
    for turn in st.session_state.chat:
        with st.chat_message(turn["role"]):
            st.markdown(link_citations(turn["content"]) if turn["role"] == "assistant" else turn["content"])
            if turn["role"] == "assistant":
                show_sources(turn["answer"])

    question = st.chat_input("Ask about these funders' grants…") or st.session_state.pop("pending", None)
    asked = sum(1 for t in st.session_state.chat if t["role"] == "user")
    if question and asked >= MAX_QUESTIONS:
        st.info(f"This demo allows {MAX_QUESTIONS} questions per visit. Refresh the page to start over.")
        return
    if not question:
        return
    with st.chat_message("user"):
        st.markdown(question)
    history = [{"role": t["role"], "content": t["content"]} for t in st.session_state.chat]
    with st.chat_message("assistant"):
        with st.status("Looking through the grants…", expanded=False) as status:
            try:
                ans = agent.ask(question, history=history,
                                on_step=lambda n, a: status.write(agent.describe_step(n, a)))
                status.update(label="Done", state="complete")
            except Exception as e:  # readable error instead of a stack trace
                status.update(label="Something went wrong", state="error", expanded=True)
                st.error(f"{type(e).__name__}: {e}")
                st.stop()
        answer = {"cited_ids": ans.cited_ids, "unknown_ids": ans.unknown_ids, "steps": ans.steps}
        st.markdown(link_citations(ans.text))
        show_sources(answer)
    st.session_state.chat += [{"role": "user", "content": question},
                              {"role": "assistant", "content": ans.text, "answer": answer}]


def link_citations(text: str) -> str:
    import re  # noqa: PLC0415

    ids = list(dict.fromkeys(re.findall(r"\[\[([\w-]+)\]\]", text)))
    urls = {g["grant_id"]: g["url"] for g in db.get(con(), ids)}
    return re.sub(r"\[\[([\w-]+)\]\]",
                  lambda m: f"[{m.group(1)}]({urls[m.group(1)]})" if m.group(1) in urls else f"{m.group(1)} ⚠️", text)


def show_sources(ans: dict) -> None:
    if not ans["cited_ids"] and not ans["unknown_ids"]:
        return
    with st.expander(f"Sources: {len(ans['cited_ids'])} grant record(s) cited"):
        rows = db.get(con(), ans["cited_ids"])
        order = {g: i for i, g in enumerate(ans["cited_ids"])}
        rows.sort(key=lambda r: order[r["grant_id"]])
        st.dataframe(
            [{"grant": r["url"], "funder": r["funder"], "grantee": r["grantee"], "purpose": r["title"],
              "dollars": r["amount_usd"], "year": r["start_year"], "theme (AI)": r["theme"]} for r in rows],
            column_config={"grant": st.column_config.LinkColumn(display_text=r"/([^/]+)/?$"),
                           "dollars": st.column_config.NumberColumn(format="dollar"),
                           "year": st.column_config.NumberColumn(format="%d")},
            hide_index=True, width="stretch")
        if ans["unknown_ids"]:
            st.warning("Cited IDs not found in the data (treat these claims as unverified): "
                       + ", ".join(ans["unknown_ids"]))
        if ans.get("steps"):
            st.caption("How it got there: " + " → ".join(ans["steps"]))
