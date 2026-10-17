# Spotify Review Insight Pipeline (multi-agent)

> Work in progress. Final results, dashboard URL, and rubric-to-evidence map will be added before submission.

Turns 660,622 Spotify Google Play reviews into a product-priority recommendation backed by traceable evidence.
Code owns record accounting, budgets, validation and arithmetic; models only answer bounded language questions.

| Stage | Role | Who | Output |
|---|---|---|---|
| 1. Prepare | ingest/profile/dedupe | code | `data/work/*.db`, ingestion report |
| 2. Enrich | topic, intent, severity | Jev `jev-1.13.0` (1 review/request) + code (sentiment, quote, entities) | `results` table |
| 3. Verify | blind re-label of a declared random sample | Claude Haiku 4.5 | `verify_report.json` |
| 4. Group | stable issue membership | code rules; Claude Haiku names issues only | `membership.csv`, `issues.json` |
| 5. Rank | priority = severity_sum | code | `ranking.csv` |
| 6. Recommend | memo + dashboard recommendations | Claude Sonnet 5.5, numbers filled in by code | `memo.md`, `claims.csv` |

## Setup

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add TYPESAFE_API_KEY and ANTHROPIC_API_KEY locally; never commit .env
```

Place the course dataset files in `data/raw/` (not committed). Source CSV SHA-256:
`1fc85de68a304dd8978b537cfa58793d5f41cbaf417fa32cb53899f83a2fcef6`.

## Commands

```sh
python -m pipeline.run data/raw/cost_100.csv --db data/work/pilot.db --scope all --out outputs/pilot           # dry run: estimates only
python -m pipeline.run data/raw/cost_100.csv --db data/work/pilot.db --scope all --out outputs/pilot --execute # paid
python -m pipeline.run --rank-only --db data/work/state.db --out outputs/final                                  # re-rank, no models
python -m cost.calculator                                                                                       # offline cost replay
python -m tests.test_pipeline_offline && python -m tests.test_calculator_offline                               # offline tests
python -m evals.system_tests                                                                                    # injection/failure tests
```

Spend controls: paid calls only with `--execute`; caps of $5 per provider and $5 per run (`pipeline/config.py`); one worker.
