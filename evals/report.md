# Investigator eval

Generated 2026-09-29T02:38:53+00:00 against `evals/tickets.jsonl`.

## Summary

| run | model | tools | n | category acc | rx acc | evidence valid | avg tool calls | $/ticket | total $ | avg latency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| haiku+tools | claude-haiku-4-5 | yes | 48 | 97.9% | 100.0% (40) | 100.0% | 3.896 | $0.0110 | $0.526 | 7.58s |
| sonnet+tools | claude-sonnet-5 | yes | 48 | 95.8% | 100.0% (40) | 100.0% | 4.146 | $0.0261 | $1.252 | 10.75s |
| haiku-no-tools | claude-haiku-4-5 | no | 48 | 100.0% | 0.0% (40) | — | 1 | $0.0028 | $0.133 | 3.49s |

The text-only baseline (claude-haiku-4-5, no tools) gets 100.0% of categories right from the complaint alone, at $0.0028 per ticket. The best tool-using run (haiku+tools) reaches 97.9%, a lift of -2.1 points for 3.9x the cost per ticket. On naming the right prescription the gap is starker: 0.0% for the baseline against 100.0% with tools — guessing an rx number from a complaint that never mentions one is close to impossible. The baseline cited nothing on 48 of 48 tickets, which is the honest outcome: with no tool output there is no evidence to cite, so its evidence validity is not comparable to a run that actually looked.

## haiku+tools

### Confusion matrix

| true \ predicted | duplicate | dropped | stale_status | phantom_schedule | no_issue_found | none |
| --- | --- | --- | --- | --- | --- | --- |
| **duplicate** | 10 | 0 | 0 | 0 | 0 | 0 |
| **dropped** | 0 | 10 | 0 | 0 | 0 | 0 |
| **stale_status** | 0 | 0 | 10 | 0 | 0 | 0 |
| **phantom_schedule** | 0 | 0 | 0 | 10 | 0 | 0 |
| **no_issue_found** | 0 | 0 | 1 | 0 | 7 | 0 |

### Calibration

| confidence | n | category accuracy |
| --- | --- | --- |
| 0.00–0.50 | 0 | — |
| 0.50–0.70 | 0 | — |
| 0.70–0.90 | 3 | 66.7% |
| 0.90–1.00 | 45 | 100.0% |

Cited nothing on 0 of 48 tickets; average 1.896 ids cited. 0 run(s) produced no diagnosis.

## sonnet+tools

### Confusion matrix

| true \ predicted | duplicate | dropped | stale_status | phantom_schedule | no_issue_found | none |
| --- | --- | --- | --- | --- | --- | --- |
| **duplicate** | 10 | 0 | 0 | 0 | 0 | 0 |
| **dropped** | 0 | 10 | 0 | 0 | 0 | 0 |
| **stale_status** | 0 | 0 | 10 | 0 | 0 | 0 |
| **phantom_schedule** | 0 | 0 | 1 | 9 | 0 | 0 |
| **no_issue_found** | 0 | 0 | 0 | 1 | 7 | 0 |

### Calibration

| confidence | n | category accuracy |
| --- | --- | --- |
| 0.00–0.50 | 0 | — |
| 0.50–0.70 | 3 | 33.3% |
| 0.70–0.90 | 17 | 100.0% |
| 0.90–1.00 | 28 | 100.0% |

Cited nothing on 0 of 48 tickets; average 1.979 ids cited. 0 run(s) produced no diagnosis.

## haiku-no-tools

### Confusion matrix

| true \ predicted | duplicate | dropped | stale_status | phantom_schedule | no_issue_found | none |
| --- | --- | --- | --- | --- | --- | --- |
| **duplicate** | 10 | 0 | 0 | 0 | 0 | 0 |
| **dropped** | 0 | 10 | 0 | 0 | 0 | 0 |
| **stale_status** | 0 | 0 | 10 | 0 | 0 | 0 |
| **phantom_schedule** | 0 | 0 | 0 | 10 | 0 | 0 |
| **no_issue_found** | 0 | 0 | 0 | 0 | 8 | 0 |

### Calibration

| confidence | n | category accuracy |
| --- | --- | --- |
| 0.00–0.50 | 44 | 100.0% |
| 0.50–0.70 | 4 | 100.0% |
| 0.70–0.90 | 0 | — |
| 0.90–1.00 | 0 | — |

Cited nothing on 48 of 48 tickets; average 0 ids cited. 0 run(s) produced no diagnosis.
