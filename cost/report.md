# 100-review pilot: measured cost and runtime

Input `data/raw/cost_100.csv` · SHA-256 `c884ac3b9be5066995d5063f96ad9af6e5e082975788c1684c4f6b6ea661dd0e` · 100 IDs · 100 unique texts · workers 1 · rates file `/Users/laurabernal/Documents/Claude/Multi Agent Pipeline/cost/rates.csv`

All numbers below are recomputed offline from `pilot_calls.jsonl` × `rates.csv`. Measured results never change when projection assumptions change.

## Measured: cold vs warm

| | Cold run (empty cache) | Warm run (saved results) |
|---|---|---|
| Completed records | 100 | 100 |
| Failed records | 0 | 0 |
| Wall-clock seconds (end to end) | 49.713 | 1.267 |
| API cost | $0.02711425 | $0.00000000 |
| Cost per 1,000 input rows | $0.27114250 | $0.00000000 |
| Cost per completed record | $0.00027114 | $0.00000000 |
| Throughput (records/s) | 2.012 | 78.927 |
| Enrichment result-cache hits | 0 | 100 |
| Claude result-cache hits | 0 | 2 |

## Measured by stage

| Run | Stage | Model | Requests | Failed | Input tok | Output tok | Missing usage | Call seconds | Cost |
|---|---|---|---|---|---|---|---|---|---|
| cold | enrich | jev-1.13.0 | 100 | 0 | 78,887 | 18,158 | 0 | 11.59 | $0.003313 |
| cold | verify | claude-haiku-4-5 | 1 | 0 | 1,746 | 1,006 | 0 | 7.97 | $0.006776 |
| cold | group | claude-haiku-4-5 | 1 | 0 | 3,467 | 706 | 0 | 9.27 | $0.006997 |
| cold | memo | claude-haiku-4-5 | 1 | 0 | 3,148 | 1,376 | 0 | 19.44 | $0.010028 |
| warm | enrich | — | 0 | 0 | 0 | 0 | 0 | 0.00 | $0.000000 |
| warm | verify | — | 0 | 0 | 0 | 0 | 0 | 0.00 | $0.000000 |
| warm | group | — | 0 | 0 | 0 | 0 | 0 | 0.00 | $0.000000 |
| warm | memo | — | 0 | 0 | 0 | 0 | 0 | 0.00 | $0.000000 |

Stage settings: {"jev_model": "jev-1.13.0", "claude_model": "claude-haiku-4-5", "claude_effort": "low", "enrich_batch_size": 1, "verify_batch_size": 25, "verify_sample": "20 (minimum) of 100", "max_tokens": {"verify": 4000, "group": 4000, "memo": 8000}}

Local compute: **unknown** — Runs on a personal laptop; electricity/hardware not metered. Reported as unknown, not zero.

## Rates used

| Model | Item | Price | Unit | Source | Checked |
|---|---|---|---|---|---|
| claude-haiku-4-5 | input | $1.0 | per 1M tokens | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-06 |
| claude-haiku-4-5 | output | $5.0 | per 1M tokens | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-06 |
| claude-opus-5-5 | input | $4.0 | per 1M tokens | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-06 |
| claude-opus-5-5 | output | $20.0 | per 1M tokens | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-06 |
| claude-sonnet-5-5 | input | $2.0 | per 1M tokens | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-06 |
| claude-sonnet-5-5 | output | $10.0 | per 1M tokens | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-06 |
| jev-1.13.0 | input | $0.042 | per 1M tokens | https://docs.typesafe.ai/llms-full.txt (Structure Recovery cookbook: "(0.042 |  0.00) $ per 1M tokens"); https://typesafe.ai ("$42 per billion input tokens") |
| jev-1.13.0 | output | $0.0 | per 1M tokens | https://docs.typesafe.ai/llms-full.txt (Structure Recovery cookbook: output 0.00) | 2026-10-06 |

## Projections (estimates, not measurements)

Basis from the cold pilot: `{"enrich_input_tokens_per_request": 788.9, "enrich_output_tokens_per_request": 181.6, "measured_enrich_retry_rate": 0.0, "enrich_seconds_per_request": 0.116, "verify_per_review": {"in": 87.3, "out": 50.3, "s": 0.39864}}`

Spending limit **$10.00** · max workers 1 · output-token caps {"enrich": null, "verify": 4000, "group": 4000, "memo": 8000} · max fallback fraction 0.0 (No stronger-model fallback is used; failed records are quarantined, not escalated.)

| Target | Exact-text reuse | Scenario | Enrich calls | Enrich | Verify | Group+memo (once) | Total API | Est. hours | Budget |
|---|---|---|---|---|---|---|---|---|---|
| declared_scope_100k | yes | base | 78,099 | $2.59 | $0.26 | $0.02 | **$2.87** | 2.61 | ok |
| declared_scope_100k | yes | conservative | 85,909 | $2.85 | $0.53 | $0.05 | **$3.43** | 5.9 | ok |
| declared_scope_100k | no | base | 100,000 | $3.31 | $0.34 | $0.02 | **$3.67** | 3.34 | ok |
| declared_scope_100k | no | conservative | 110,000 | $3.64 | $0.68 | $0.05 | **$4.37** | 7.55 | ok |
| full_corpus | yes | base | 484,189 | $16.04 | $1.64 | $0.02 | **$17.70** | 16.13 | ⚠️ OVER |
| full_corpus | yes | conservative | 532,608 | $17.65 | $3.28 | $0.05 | **$20.98** | 36.47 | ⚠️ OVER |
| full_corpus | no | base | 660,609 | $21.89 | $2.24 | $0.02 | **$24.14** | 22.01 | ⚠️ OVER |
| full_corpus | no | conservative | 726,670 | $24.08 | $4.48 | $0.05 | **$28.60** | 49.75 | ⚠️ OVER |

Group and memo are fixed overhead counted once per run (never multiplied by review count). The conservative case adds retries, doubles the verification sample, triples fixed-stage cost and halves throughput.

## Replay

```
python -m cost.calculator            # offline, no API key
```
