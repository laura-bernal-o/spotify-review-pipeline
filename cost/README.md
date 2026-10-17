# Cost and runtime calculator

## Offline replay (default — no API key, no calls)

```sh
python -m cost.calculator
```

Recomputes every charge as `billed_units × price_per_unit` from `pilot_calls.jsonl` and `rates.csv`, then projects
full-run scenarios from `assumptions.json`. Writes `report.md` and `usage.csv`. Edit `rates.csv` or `assumptions.json`
and re-run; measured pilot results never change when projections change.

## Paid pilot (explicit, separate command)

```sh
python -m cost.calculator pilot            # shows the plan + estimates, no calls
python -m cost.calculator pilot --execute  # real cold run, then warm run, on data/raw/cost_100.csv
```

Checks the `cost_100.csv` checksum against `manifest.json`, then runs every stage (enrich, verify, group, rank,
memo) with an empty result cache and one worker (cold), and again with saved results (warm). Refuses to overwrite
an existing pilot.

## Files

| File | Contents |
|---|---|
| `pilot_records.jsonl` | one final record per pilot ID (grading-contract format) |
| `pilot_calls.jsonl` | every attempted call: run, stage, model, outcome, usage, timing, request ID |
| `pilot_meta.json` | input checksum, settings, wall-clock time and record counts for cold and warm runs |
| `rates.csv` | editable prices with units, sources and check dates |
| `usage.csv` | each charged item mapped to its call, units, price and cost |
| `assumptions.json` | spending limit, workers, output caps, fallback limit, projection targets and scenarios |
| `report.md` | measured cold/warm tables, stage breakdown, rates and projections |

Local compute is reported as **unknown** (personal laptop, not metered), not as zero.
Offline tests of these rules (with fake responses): `python -m tests.test_calculator_offline`.
