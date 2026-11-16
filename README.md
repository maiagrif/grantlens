# GrantLens

**AI-assisted analysis of any funder's grants, with the AI's accuracy measured against the funders' own categories.**

GrantLens has Claude read and categorize tens of thousands of public grant records into one shared set of themes, strategies and populations, so different funders' portfolios can be compared side by side. Before trusting those labels, it measures them, without hand-labeling: each funder's staff already sorted its grants into programs and topics, so those categories become the answer key for theme. The AI never sees them. Agreement is scored with Cohen's kappa.

**Live demo:** _[add your Streamlit link]_

> Uses public grant records from the [Mott Foundation](https://www.mott.org/grants/) and the [Gates Foundation](https://www.gatesfoundation.org/about/committed-grants). Independent project, not affiliated with or endorsed by either foundation.

## Results

Validation sample: 300 random grants (150 per funder) whose funder category maps to exactly one theme. Kappa is agreement beyond chance (1 = perfect, 0 = chance).

| Labeler | Theme κ vs funder, both | Mott | Gates | Est. cost to label all 32k grants |
| --- | --- | --- | --- | --- |
| Keyword baseline (free) | 0.55 | 0.61 | 0.43 | $0 |
| **Claude Haiku 5.5** (chosen) | 0.69 | 0.58 | 0.75 | ~$2 |
| Claude Sonnet 5.5 | 0.73 | 0.63 | 0.80 | ~$33 |
| Claude Opus 5.5 | 0.71 | 0.61 | 0.78 | ~$68 |

**Full run (Haiku, every grant with a funder theme):** Haiku labeled 32,092 of 32,179 in-scope grants for $1.54. The other 87 are Gates pathogen-research grants (virus strains, polio, Nipah, TB aerosols) that the model declines on safety grounds even for categorization. They stay unlabeled rather than being filled in by another model. Against the 21,862 grants with a funder theme, it scored κ 0.75 (81% agree) vs 0.55 for the keyword baseline. That breaks down as Gates 0.77 vs 0.47 and Mott 0.63 vs 0.63. Its “high”-confidence labels matched the funder 87% of the time and its “low”-confidence ones 33%.

Strategy and population have no answer key (funders don't record them), so models are checked against each other: Claude models agree with one another at κ 0.73–0.95 on every field, while the keyword baseline agrees with them at only 0.21–0.44.

**Findings**

- **Claude adds most where records are thin.** On Gates's one-line purpose statements, Haiku matches the funder's topic 83% of the time vs 56% for keyword rules.
- **On Mott it only ties the baseline, and the disagreements explain why.** Mott's program areas are broad: “Restoring Community Vitality” includes small-business incubators, which the AI calls Economic Opportunity. Most disagreements are defensible second opinions. The answer key was fixed before seeing results and not tuned afterwards.
- **Bigger isn't better here.** Opus costs twice Sonnet and scores no higher; Haiku is within 0.04 κ of the best at about 1/20 of the price, so it labeled the full dataset.
- **Confidence is informative.** Labels the AI marks “high” match the funder ~80% of the time; “low” ~50%, so low-confidence grants are the ones to route to a person.

## What's in the app

| Page | What it shows |
| --- | --- |
| **Compare funders** | Each funder's share of dollars by theme, strategy and population, plus how each theme's share changed year by year |
| **Explore a funder** | Filter by place or program (e.g. "Flint"), compare two periods, see the largest grantees and every grant with its labels |
| **Check the AI** | Each model's theme agreement with the funders' own categories (per funder, on the sample or every grant), a confusion matrix, whether the AI's confidence predicts its errors, every disagreement with the AI's stated reason, and model-to-model agreement on strategy and population. An optional hand spot-check runs locally. |
| **Ask** | Plain-English questions answered by Claude with read-only tools. Every grant mentioned is cited and linked, and every number comes from a SQL query |

## How it works

```
sources/mott.py  (crawl mott.org) ─┐
sources/gates.py (download CSV)  ──┼─> db.py ──> data/grantlens.db ──> app.py (Streamlit)
                                   │      ▲                              ├─ Compare / Explore  (charts.py)
taxonomy.py  (shared categories) ──┤      │                              ├─ Check the AI        (benchmark.py)
label.py     (Claude, Batch API) ──┴─> data/labels/<model>.jsonl         └─ Ask                 (agent.py)
baseline.py  (keyword rules)     ──────┘
```

- **One taxonomy** (`taxonomy.py`) drives the labeling prompt, the JSON schema Claude must answer in, the hand spot-check form and the dashboard.
- **A free answer key.** `data/funder_category_map.csv` maps each funder category (Gates topic, Mott program | initiative) to one shared theme, or leaves it blank when the category spans themes. About 68% of grants per funder get an unambiguous reference theme.
- **Structured outputs** guarantee every label is a valid category. The Batch API labels everything at half price.
- **No leakage.** The labelers (Claude and the keyword baseline) never see the funder's program or topic, so they can't copy the answer. Kappa bands follow Landis & Koch, the convention in health research.
- **Cheapest model that's good enough.** `label.py validate` runs several models on the same sample and reports accuracy alongside measured cost, so model choice is a data-driven decision.
- **Safe by construction.** The database is opened read-only, and the Ask tool accepts only a single `SELECT`.

## Run it

Requires [uv](https://docs.astral.sh/uv/) and an [Anthropic API key](https://console.anthropic.com/) (with a spend limit set).

```bash
uv sync
export ANTHROPIC_API_KEY=...
uv run python db.py                         # build the database from the included data
uv run python label.py validate haiku sonnet opus   # ~$2: each model labels the same 300 grants
uv run python db.py && uv run python benchmark.py   # score models vs funder categories (also on the "Check the AI" page)
uv run python label.py submit haiku         # label all ~32k in-scope grants with the model you chose (asks first)
uv run python label.py collect              # after the batch finishes (usually < 1 hour)
uv run python db.py
uv run streamlit run app.py
uv run python evals/run_evals.py            # score the Ask tab on 11 questions with SQL-computed answers
```

Offline tests, with no key needed: `uv run pytest`.

To refresh the raw data: `uv run python -m sources.mott` (~30–60 min, polite crawl, cached) and `uv run python -m sources.gates`.

## Add a funder

1. Write `sources/<funder>.py` with a `FUNDER` name and a `load()` that returns records with the fields in `db.COLUMNS`.
2. Add it to the loop in `db.build()` and, if needed, adjust `SCOPE_SQL` for which years to label.
3. Run `db.py`, `label.py submit`, `label.py collect`, then `db.py` again. The dashboard picks it up automatically.

The labeling, the accuracy check and the dashboard are not specific to grants. The same pattern applies to any pile of short texts that needs consistent categories, such as survey responses, clinical notes or support tickets.

## Deploy (Streamlit Community Cloud, free)

1. Push to GitHub, including `data/grantlens.db` (about 50 MB) and `data/labels/`.
2. At [share.streamlit.io](https://share.streamlit.io), create an app from the repo with `app.py` as the entry point.
3. Under **Advanced settings → Secrets**, add:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   GRANTLENS_READONLY = "1"           # hides the hand spot-check on the public site
   MAX_QUESTIONS_PER_SESSION = "15"
   # APP_PASSCODE = "..."             # optional: require a code
   ```
4. Keep a monthly spend limit on the key. The Ask page is the only part that calls Claude on the live site.

## Limitations

- **The records describe intent, not results.** They hold descriptions, amounts, dates and places, with no outcomes. Inside a foundation, the same design would run on the grants-management system.
- **Records differ by funder.** Mott's descriptions are paragraphs; Gates's are one-line purpose statements, so Gates labels rest on less text. The accuracy page reports agreement per funder sample for exactly this reason.
- **Dates differ by funder.** Mott dates are grant-period starts; Gates dates are commitment months.
- **Single labels.** Each grant gets one primary theme. A grant that spans two themes counts only toward the primary one.
- **The answer key is imperfect.** Funder categories reflect internal program structure, not a neutral reading of each grant, and only ~68% of grants map to one theme. Treat agreement as a lower bound on accuracy.
- **87 grants unlabeled.** Pathogen-research grant descriptions trigger model refusals, so a small slice of Gates's Health research is missing from the charts.
- **Strategy and population are checked only for consistency.** Models agreeing doesn't prove they're right.
