"""Offline tests: no API key needed. Claude calls are replaced with canned responses."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

import agent  # noqa: E402
import baseline  # noqa: E402
import benchmark  # noqa: E402
import db  # noqa: E402
import label  # noqa: E402
import metrics  # noqa: E402
import taxonomy  # noqa: E402
from run_evals import check, numbers_in  # noqa: E402

CON = db.connect()


def run_page(name: str) -> AppTest:
    at = AppTest.from_string(f"import views\nviews.{name}()", default_timeout=60)
    return at.run()


@pytest.fixture
def tmp_human(tmp_path, monkeypatch):
    path = tmp_path / "human_labels.csv"
    monkeypatch.setattr(metrics, "HUMAN_PATH", path)
    return path


# ---------------------------------------------------------------- data

def test_both_funders_loaded():
    ov = {o["funder"]: o for o in db.overview(CON)}
    assert ov["Mott Foundation"]["grants"] > 8000 and ov["Gates Foundation"]["grants"] > 40000
    assert ov["Gates Foundation"]["in_scope"] < ov["Gates Foundation"]["grants"]  # only 2015+


def test_sql_tool_is_read_only():
    assert "error" in agent.query_grants_sql("DROP TABLE grants")
    assert "error" in agent.query_grants_sql("SELECT 1; DELETE FROM grants")


# ---------------------------------------------------------------- labeling

def test_label_request_and_parse():
    g = db.get(CON, ["INV-023730"])[0]
    p = label.request_params("claude-haiku-5-5", g)
    assert p["output_config"]["format"]["schema"] is taxonomy.LABEL_SCHEMA
    assert "fallbacks" not in p  # each label must come from the model being measured
    good = {"theme": "Health", "strategy": "Research & Evaluation", "population": "Not Specified",
            "confidence": "high", "rationale": "malaria diagnostics"}
    msg = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(good))])
    assert label.parse_label(msg, "INV-023730", "m")["theme"] == "Health"
    assert label.parse_label(SimpleNamespace(stop_reason="refusal", content=[]), "x", "m") is None


def test_baseline_labels_are_valid_values():
    for g in db.get(CON, label.sample_ids()[:30]):
        lab = baseline.label(g)
        assert all(lab[f] in taxonomy.FIELDS[f] for f in taxonomy.FIELDS)


def test_cost_math():
    u = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_write": 0, "cache_read": 0}
    assert label.cost("claude-opus-5-5", u, batch=False) == 4.0
    assert label.cost("claude-opus-5-5", u, batch=True) == 2.0


# ---------------------------------------------------------------- accuracy

def test_kappa_matches_textbook():
    a = ["y"] * 25 + ["y"] * 25 + ["n"] * 5 + ["n"] * 45
    b = ["y"] * 25 + ["n"] * 25 + ["y"] * 5 + ["n"] * 45
    assert round(metrics.cohens_kappa(a, b), 3) == 0.4
    assert metrics.interpret(0.85) == "almost perfect"


def test_agreement_against_baseline(tmp_human):
    ids = label.sample_ids()[:20]
    labs = metrics.model_labels(CON, baseline.MODEL, ids)
    for i, gid in enumerate(ids):  # agree on everything except the theme of every 4th grant
        lab = labs[gid]
        theme = "Other" if i % 4 == 0 and lab["theme"] != "Other" else lab["theme"]
        metrics.save_human(gid, theme, lab["strategy"], lab["population"])
    row = next(r for r in metrics.agreement(CON) if r["model"] == baseline.MODEL)
    assert row["grants compared"] == 20 and row["strategy agree %"] == 100 and row["theme agree %"] == 75
    assert len(metrics.disagreements(CON, baseline.MODEL)) == 5


# ---------------------------------------------------------------- pages

def test_compare_page():
    at = run_page("page_compare")
    assert not at.exception, at.exception
    assert len(at.get("vega_lite_chart")) >= 3


def test_explore_page_with_place_filter():
    at = run_page("page_explore")
    at.selectbox[0].set_value("Mott Foundation").run()
    at.text_input[0].set_value("Flint").run()
    assert not at.exception, at.exception
    assert "matching “Flint”" in " ".join(c.value for c in at.caption)


def test_hand_spot_check_saves(tmp_human):
    at = run_page("hand_spot_check")
    assert not at.exception, at.exception
    first = label.sample_ids()[0]
    at.radio[0].set_value("Education")
    at.radio[1].set_value("Direct Services")
    at.radio[2].set_value("Children & Youth")
    at.button[0].click().run()
    assert not at.exception, at.exception
    saved = metrics.load_human()[first]
    assert (saved["theme"], saved["strategy"], saved["population"]) == ("Education", "Direct Services", "Children & Youth")


def test_check_page_results(tmp_human):
    for gid in label.sample_ids()[:10]:
        metrics.save_human(gid, "Health", "Direct Services", "Not Specified")
    at = run_page("page_check")
    assert not at.exception, at.exception
    assert len(at.dataframe) >= 2
    assert len(at.get("vega_lite_chart")) >= 1
    at.radio[1].set_value("Every grant with a funder theme").run()
    assert not at.exception, at.exception


# ---------------------------------------------------------------- benchmark

def test_labelers_never_see_funder_categories():
    g = db.get(CON, ["INV-023730"])[0]
    text = taxonomy.grant_text(g)
    for cat in benchmark.categories(g):
        assert cat not in text


def test_category_map_uses_real_themes():
    assert set(benchmark.category_map().values()) - {""} <= set(taxonomy.THEMES)


def test_reference_theme_requires_one_theme():
    g = {"funder": "Gates Foundation", "program": "", "subprogram": "Malaria|HIV"}
    assert benchmark.reference_theme(g) == "Health"
    g["subprogram"] = "Malaria|Agricultural Development"
    assert benchmark.reference_theme(g) is None
    g["subprogram"] = "Malaria|Global Health and Development Public Awareness and Analysis"
    assert benchmark.reference_theme(g) == "Health"  # blank (ambiguous) categories don't count


def test_sample_is_all_scorable():
    refs = benchmark.references()
    ids = label.sample_ids()
    assert len(ids) == 300 and all(g in refs for g in ids)


def test_baseline_scored_against_funder():
    row = next(r for r in benchmark.theme_accuracy(CON, sample_only=True) if r["model"] == baseline.MODEL)
    assert row["grants compared"] == 300 and 0 < row["kappa"] < 1
    conf = benchmark.confusion(CON, baseline.MODEL, sample_only=True)
    assert sum(r["grants"] for r in conf) == 300


def fake_ask(question, history=None, on_step=None, client=None):
    if on_step:
        on_step("search_grants", {"query": "test"})
    return agent.Answer(text="See [[INV-023730]] and [[2026-14727]], not [[0000-00000]].",
                        cited_ids=["INV-023730", "2026-14727"], unknown_ids=["0000-00000"], steps=["Searched"])


def test_ask_page():
    with patch.object(agent, "ask", fake_ask):
        at = run_page("page_ask")
        at.chat_input[0].set_value("anything").run()
        assert not at.exception, at.exception
        md = " ".join(m.value for m in at.markdown)
        assert "gatesfoundation.org" in md and "mott.org/grants" in md
        assert any("unverified" in w.value for w in at.warning)


# ---------------------------------------------------------------- evals

def test_numbers_in():
    assert 12_400_000 in numbers_in("about $12.4 million in total")
    assert 14 in numbers_in("$1,250,000 across 14 grants")
    assert 2026 not in numbers_in("see [[2026-14727]]")


def test_every_eval_question_has_an_answer():
    for case in yaml.safe_load((ROOT / "evals/questions.yaml").read_text()):
        if "expected_sql" in case:
            rows = db.run_select(CON, case["expected_sql"], max_rows=5000)["rows"]
            assert rows and rows[0][0] not in (None, 0), case["id"]


def test_check_decline():
    dec = {"kind": "decline", "expect_any": ["does not include"]}
    assert check(dec, agent.Answer("The data does not include outcomes.", [], []), CON)[0]
    assert not check(dec, agent.Answer("The data does not include outcomes.", [], ["9-9"]), CON)[0]


def test_agreement_by_funder(tmp_human):
    ids = label.sample_ids()
    for gid in ids[:20]:
        metrics.save_human(gid, "Health", "Direct Services", "Not Specified")
    funders = {g["grant_id"]: g["funder"] for g in db.get(CON, ids[:20])}
    for f in ("Mott Foundation", "Gates Foundation"):
        row = next(r for r in metrics.agreement(CON, f) if r["model"] == baseline.MODEL)
        assert row["grants compared"] == sum(v == f for v in funders.values())
