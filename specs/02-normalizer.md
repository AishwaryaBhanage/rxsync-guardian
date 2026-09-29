# Spec 02: Normalizer-lite

## User story
As the builder of RxSync Guardian, I want every raw record from PMS A, B and C rewritten in one standard form and loaded into Postgres, so duplicate and desync detection compare like with like.

## Inputs
- `data/raw/pms_a.csv`, `pms_b.json`, `pms_c.txt` from the simulator.
- CLI: `uv run python -m normalizer.run --in data/raw`

## Design
- One YAML mapping per format in `normalizer/mappings/` (pms_a.yaml, pms_b.yaml, pms_c.yaml): source field → canonical field, plus date and status formats. Drafted by Claude Code from sample records, approved by me. No LLM calls at runtime.
- Drug names: rule-based cleanup first (lowercase, strip salt words like "calcium", standardize units "MG" → "mg", forms "TAB" → "tablet"), then a small alias table `normalizer/drug_aliases.yaml` for anything left.
- Any drug name that still can't be mapped is reported and makes the run fail; never guess.
- Statuses map to a fixed set: received, in_process, ready, picked_up, delivered.
- All dates become ISO format.

## Outputs (Postgres)
- `rx_records`: one row per raw record with source_pms, source_file, source_line, pharmacy_id, rx_number, patient first/last name, patient_dob, drug_name, strength, form, days_supply, refills_left, status, status_ts, refill_on_schedule.
- `rx_events`: status history where the source provides it.
- `rejects`: records that couldn't be parsed, with the reason.
- Schema in `normalizer/schema.sql`.

## Acceptance criteria
- [ ] One command loads all three formats.
- [ ] Row count in rx_records equals the number of records in the raw files, minus rejects.
- [ ] Every row keeps its source (pms, file, line).
- [ ] 100% of drug names map to a canonical name; an unmapped name fails the run with a clear message.
- [ ] For records that are not planted duplicates, drug, strength and patient fields match the truth data.
- [ ] Running twice gives the same table contents (no double rows).
- [ ] Fast tests use the small world; full-scale checks are marked slow.

## Edge cases
- An unknown status or unparseable date → the record goes to rejects, and the run continues.
- Extra spaces or odd casing in names are cleaned.

## Out of scope
- Detecting duplicates (Phase 3).
- RxNorm codes (next step, not built).