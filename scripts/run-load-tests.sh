#!/bin/bash
set -e

BASE_URL="${BASE_URL:-https://api.pipelineiq.YOURDOMAIN.com}"
AUTH_TOKEN="${AUTH_TOKEN:-}"
RESULTS_DIR="k6-results"

mkdir -p "$RESULTS_DIR"

echo "Running load tests against: $BASE_URL"
echo "Results will be saved to: $RESULTS_DIR/"

echo ""
echo "=== Test 1: Authentication (100 concurrent users) ==="
k6 run \
  --env BASE_URL="$BASE_URL" \
  --env TEST_EMAIL="loadtest@pipelineiq.test" \
  --env TEST_PASSWORD="LoadTest@2024!" \
  --summary-export="$RESULTS_DIR/auth-summary.json" \
  k6/load-auth.js

echo ""
echo "=== Test 2: YAML Validation (200 req/s) ==="
k6 run \
  --env BASE_URL="$BASE_URL" \
  --env AUTH_TOKEN="$AUTH_TOKEN" \
  --summary-export="$RESULTS_DIR/validate-summary.json" \
  k6/load-pipeline-api.js

echo ""
echo "=== Test 3: File Upload (50 concurrent) ==="
k6 run \
  --env BASE_URL="$BASE_URL" \
  --env AUTH_TOKEN="$AUTH_TOKEN" \
  --summary-export="$RESULTS_DIR/upload-summary.json" \
  k6/load-file-upload.js

echo ""
echo "All load tests complete. Results in $RESULTS_DIR/"

for f in "$RESULTS_DIR"/*.json; do
  [ -f "$f" ] || continue
  thresholds_ok=$(python3 -c "
import json
data = json.load(open('$f'))
failed = [k for k, v in data.get('metrics', {}).items() if 'thresholds' in v and not all(t['ok'] for t in v['thresholds'])]
print('FAILED:', failed if failed else 'NONE')
  " 2>/dev/null || echo "could not parse")
  echo "$f: $thresholds_ok"
done
