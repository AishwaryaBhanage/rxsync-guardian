# Investigator eval

Generated 2026-09-29T04:03:40+00:00 against `evals/tickets.jsonl`.

## Summary

| run | model | tools | n | category acc | rx acc | evidence valid | avg tool calls | $/ticket | total $ | avg latency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| haiku+tools | claude-haiku-4-5 | yes | 48 | 97.9% | 100.0% (40) | 100.0% | 4.104 | $0.0124 | $0.597 | 8.57s |

No baseline/tool pair was run, so there is nothing to compare.

## haiku+tools

### Confusion matrix

| true \ predicted | duplicate | dropped | stale_status | phantom_schedule | no_issue_found | none |
| --- | --- | --- | --- | --- | --- | --- |
| **duplicate** | 10 | 0 | 0 | 0 | 0 | 0 |
| **dropped** | 0 | 10 | 0 | 0 | 0 | 0 |
| **stale_status** | 0 | 0 | 10 | 0 | 0 | 0 |
| **phantom_schedule** | 0 | 0 | 0 | 10 | 0 | 0 |
| **no_issue_found** | 0 | 0 | 0 | 1 | 7 | 0 |

### Calibration

| confidence | n | category accuracy |
| --- | --- | --- |
| 0.00–0.50 | 0 | — |
| 0.50–0.70 | 0 | — |
| 0.70–0.90 | 3 | 100.0% |
| 0.90–1.00 | 45 | 97.8% |

Cited nothing on 0 of 48 tickets; average 1.771 ids cited. 0 run(s) produced no diagnosis.
