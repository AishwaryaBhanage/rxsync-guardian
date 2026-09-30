#!/usr/bin/env bash
# Assemble the Lambda package in infra/build/lambda/. Terraform zips that folder.
#
#   scripts/build_lambda.sh
#
# Contents: api/handler.py, investigator/, evals/ (code, reports, tickets), the
# anthropic SDK and its dependencies for Linux x86_64 / Python 3.12, and the
# simulator CSVs the tools read, regenerated from the default seed. pandas and
# numpy are NOT bundled: the AWS SDK for pandas layer provides them.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/infra/build/lambda"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

rm -rf "$OUT"
mkdir -p "$OUT"
cd "$ROOT"

# 1. Third-party packages, pinned to uv.lock via a constraints file so the
#    deployed versions match the ones the tests ran against.
uv export --frozen --no-dev --no-hashes --no-emit-project --quiet > "$TMP/constraints.txt"
uv pip install --quiet \
  --target "$OUT" \
  --python-platform x86_64-manylinux2014 \
  --python-version 3.12 \
  --only-binary :all: \
  --constraint "$TMP/constraints.txt" \
  anthropic python-dotenv

# 2. Our code. api/local.py is left out: it needs FastAPI and is for local use only.
mkdir -p "$OUT/api" "$OUT/investigator" "$OUT/evals"
cp api/__init__.py api/handler.py "$OUT/api/"
cp investigator/*.py "$OUT/investigator/"
cp evals/*.py "$OUT/evals/"
# The eval reports the /report route serves, and the ticket set behind /examples.
cp evals/report.json evals/report_v1.json evals/report_v2.json evals/tickets.jsonl "$OUT/evals/"

# 3. Data from the default seed (the CLI defaults; byte-identical on every build).
#    Only the files tools.py reads. patients.csv, the one file with names, stays out.
uv run python -m simulator.generate --seed 42 --out "$TMP/data" > /dev/null
mkdir -p "$OUT/data/truth"
cp "$TMP/data/app_view.csv" "$OUT/data/"
cp "$TMP/data/truth/prescriptions.csv" "$TMP/data/truth/fill_events.csv" \
   "$TMP/data/truth/pharmacies.csv" "$OUT/data/truth/"

find "$OUT" -name "__pycache__" -type d -prune -exec rm -rf {} +
echo "built $(du -sh "$OUT" | cut -f1) in ${OUT#"$ROOT"/}"
