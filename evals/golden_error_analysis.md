# Golden-50 evaluation and error analysis

Human labels: `evals/golden_50_labeled.csv` (hand-labeled by the author in `labeling/golden_50_labeling.xlsx`).
Predictions: the final 100k run (`data/work/state.db`, label config `jev-1.13.0+labels-v1-short+schema-a5-v1+q=topic,intent,severity`).
Per-case results: `evals/golden_results.json` / `.csv`. Command: `python -m evals.golden_eval --db data/work/state.db`.
Human labels were never model inputs, examples, or tuning data; no prompt or setting was changed after seeing these results.

## Results (n = 50)

| Field | Result |
|---|---|
| Topic agreement | 0.70 (35/50) |
| Intent agreement | 0.88 (44/50) |
| Severity exact agreement | 0.76 (38/50) |
| Severity mean absolute error | 0.30 |
| Sentiment MAE / within declared ±0.5 tolerance | 0.22 / 0.84 |
| Evidence quote is an exact source substring | 1.00 |
| `needs_review` as a prediction | 1 true positive, 5 false positives, 0 false negatives |
| Cases the human marked ambiguous | 7 |
| Missing predictions | 0 |

Fifty cases are a small diagnostic sample, not a population accuracy estimate.

### Disclosure: one human revision after a first evaluation

The first evaluation showed severity exact agreement 0.16 (MAE 1.76). Inspection showed the human severity column had
been filled as an intensity-of-feeling scale (e.g. "Great app use it every chance I get" = 4), not the shared impact
scale where praise is 1. The author was shown the shared severity definitions plus five example reviews (with the
pipeline's severity for those five), then re-labeled **only the severity column** of all 50 against the shared
definitions. Topic, intent and sentiment labels were not changed. Because predictions for five cases were visible,
this revision is not fully blind; the severity figures should be read with that caveat. After the
revision: severity exact 0.76, MAE 0.30. No pipeline prompt, threshold or setting was changed in response.

## Topic confusion (human → predicted)

| Human \ Pred | other | usability | catalog | billing | playback | downloads |
|---|---|---|---|---|---|---|
| other (28) | 23 | 1 | 3 | 1 | | |
| usability (15) | 3 | 8 | 2 | 2 | | |
| playback (6) | | | 1 | | 4 | 1 |
| support (1) | | | | | 1 | |

## Error analysis

| Pattern | n | Human | Pipeline | Which the shared definitions support |
|---|---|---|---|---|
| General praise ("I absolutely love Spotify…") | 2 | usability | other | Pipeline — "general praise is `other`" |
| Praise of recommendations | 2 | usability | catalog | Pipeline — recommendations belong to `catalog` |
| Premium-only features, paywalls, price + ads | 2 | usability | billing | Pipeline — "explicitly premium-only controls go to `billing`"; mixed price/ads case is ambiguous |
| Paying user cannot use offline mode | 1 | playback | downloads | Pipeline — offline listening is `downloads` |
| Pure feature request ("organize albums into collections") | 1 | severity 3 | severity 1 | Pipeline — a pure feature request is severity 1 |
| Non-English text (Tagalog, Indonesian, Hindi) | 4 | other/unclear (marked ambiguous) | specific topic | Genuinely ambiguous: Jev interprets the language (e.g. Indonesian "lyrics sometimes missing" → catalog), the human chose `unclear` for unsupported language |
| Review with an "Update: support fixed it" preface | 1 | support/request/1 | playback/complaint/4 | **Human** — the pipeline labeled the resolved old problem; a real enricher failure |
| "We are boycotting Swedish app…" | 1 | complaint | cancellation | Debatable — explicit personal boycott vs. "bare boycott slogan" |
| Two problems (lyrics not loading + cannot seek) | 1 | playback/4 | catalog/3 | Debatable — highest-severity rule; human judged seeking a blocked core task |

**Takeaways.** Most topic disagreements are boundary cases where the written definitions favour the pipeline's label,
so the 0.70 topic figure understates agreement with the shared rubric but correctly shows that topic boundaries
(usability vs. other/catalog/billing) are where both humans and the model are least consistent. Real enricher
weaknesses: (1) reviews whose later text supersedes earlier complaints, (2) non-English text, which the model
labels confidently instead of routing to `unclear`/`needs_review`, and (3) occasional severity-5 over-scoring
(seen outside the golden set, e.g. "Great app poorly executed." scored 5). `needs_review` (confidence < 0.5)
over-flags: 5 of 6 flagged golden cases were labeled confidently by the human, and it missed none of the human's
needs-review cases.

## Independent verifier (separate from the golden set)

Claude Haiku 4.5 re-labeled a declared 1% random sample of directly classified records (n = 781) without seeing the
enricher's answer: topic 0.83, intent 0.90, severity exact 0.81, severity MAE 0.22 (`outputs/final/verify_report.json`).
System tests (injection, planted wrong label, malformed output, transient API failure): all passed — see
`evals/system_tests.json`.
