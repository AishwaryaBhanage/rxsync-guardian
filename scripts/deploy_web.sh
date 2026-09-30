#!/usr/bin/env bash
# Build web/ against the deployed API and publish it to S3 + CloudFront.
#
#   scripts/deploy_web.sh
#
# Needs: `terraform apply` done in infra/, AWS credentials, and a DEMO_KEY=...
# line in the repo-root .env holding the same value as the DEMO_KEY secret.
#
# Note: VITE_ variables are compiled into the public JavaScript bundle, so the
# demo key is visible to anyone who loads the site. It is a demo gate, not auth.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
tf_output() { terraform -chdir="$ROOT/infra" output -raw "$1"; }

API_URL="$(tf_output function_url)"
BUCKET="$(tf_output web_bucket)"
DISTRIBUTION="$(tf_output distribution_id)"
SITE_URL="$(tf_output site_url)"

# Read only the DEMO_KEY line from .env rather than sourcing the whole file, strip
# optional surrounding quotes, and never echo it.
DEMO_KEY="$(grep -E '^DEMO_KEY=' "$ROOT/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
DEMO_KEY="${DEMO_KEY%\"}"; DEMO_KEY="${DEMO_KEY#\"}"
DEMO_KEY="${DEMO_KEY%\'}"; DEMO_KEY="${DEMO_KEY#\'}"
if [[ -z "$DEMO_KEY" ]]; then
  echo "error: no DEMO_KEY=... line in $ROOT/.env" >&2
  exit 1
fi

# Shell variables take precedence over web/.env.local, so the local-dev values
# in that file cannot leak into the production build.
cd "$ROOT/web"
npm ci --silent
VITE_API_URL="${API_URL%/}" VITE_DEMO_KEY="$DEMO_KEY" npm run build

# Hashed assets can be cached forever; index.html must always be revalidated so
# a new deploy is picked up. --delete removes files from older builds.
aws s3 sync dist "s3://$BUCKET" --delete --exclude index.html \
  --cache-control "public, max-age=31536000, immutable"
aws s3 cp dist/index.html "s3://$BUCKET/index.html" --cache-control "no-cache"

aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" --paths "/*" \
  --query "Invalidation.Id" --output text > /dev/null

echo "deployed: $SITE_URL (CloudFront may take a minute to pick it up)"
