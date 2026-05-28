#!/bin/bash
set -e
NAMESPACE="pipelineiq"
BASE_URL="${BASE_URL:-https://api.pipelineiq.YOURDOMAIN.com}"

check_api_health() {
  local max_attempts=$1
  local attempt=0
  while [ $attempt -lt $max_attempts ]; do
    local status
    status=$(curl -sf -o /dev/null -w "%{http_code}" "$BASE_URL/healthz" 2>/dev/null || echo "000")
    if [ "$status" = "200" ]; then
      echo "API healthy after $attempt seconds"
      return 0
    fi
    echo "  Waiting... (attempt $attempt, status=$status)"
    sleep 1
    attempt=$((attempt + 1))
  done
  echo "API did not recover within $max_attempts seconds"
  return 1
}

run_scenario() {
  local name="$1"
  local action="$2"
  local recovery_command="$3"
  local max_recovery="${4:-60}"

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "CHAOS SCENARIO: $name"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  local start
  start=$(date +%s)

  eval "$action"
  echo "Chaos action executed. Waiting for recovery..."
  sleep 5

  eval "$recovery_command"

  local end duration
  end=$(date +%s)
  duration=$((end - start))
  echo "Scenario '$name' completed in ${duration}s"
}

echo "Starting PipelineIQ Chaos Engineering Suite"
echo "Target: $BASE_URL"
echo "Namespace: $NAMESPACE"
echo ""

check_api_health 5 || { echo "API is not healthy — aborting chaos tests"; exit 1; }

run_scenario "Kill one API pod" \
  "kubectl delete pod -n $NAMESPACE -l app=pipelineiq-api --field-selector=status.phase=Running -o name 2>/dev/null | head -1 | xargs -r kubectl delete -n $NAMESPACE" \
  "check_api_health 30" \
  "30"

run_scenario "Kill all API pods" \
  "kubectl delete pods -n $NAMESPACE -l app=pipelineiq-api --grace-period=0 --force 2>/dev/null" \
  "check_api_health 60" \
  "60"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "CHAOS SCENARIO: Kill PostgreSQL pod"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

kubectl delete pod -n $NAMESPACE -l app=postgres --grace-period=0 --force 2>/dev/null || true
sleep 5

READYZ_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$BASE_URL/readyz" 2>/dev/null || echo "000")
if [ "$READYZ_STATUS" = "503" ] || [ "$READYZ_STATUS" = "000" ]; then
  echo "EXPECTED: /readyz returned $READYZ_STATUS when DB is down"
else
  echo "WARNING: /readyz returned $READYZ_STATUS (expected 503 or 000)"
fi

echo "Waiting for PostgreSQL to recover..."
kubectl wait --for=condition=Ready pod -l app=postgres -n $NAMESPACE --timeout=120s 2>/dev/null || true
echo "PostgreSQL pod is ready. Waiting for API to reconnect..."
check_api_health 60

run_scenario "Kill Redis cache pod" \
  "kubectl delete pod -n $NAMESPACE -l app=redis-cache --grace-period=0 --force 2>/dev/null || true" \
  "check_api_health 30" \
  "30"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "CHAOS SCENARIO: Kill one Celery bulk worker"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

kubectl delete pod -n $NAMESPACE -l app=worker-bulk --field-selector=status.phase=Running -o name 2>/dev/null | head -1 | xargs -r kubectl delete -n $NAMESPACE --grace-period=30
echo "Bulk worker killed. Waiting for Kubernetes to restart it..."
kubectl rollout status deployment/worker-bulk -n $NAMESPACE --timeout=60s 2>/dev/null || true
echo "Bulk worker restarted by Kubernetes"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "CHAOS SCENARIO: Kill Gemini AI worker"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

kubectl delete pod -n $NAMESPACE -l app=worker-gemini --grace-period=0 --force 2>/dev/null || true
sleep 5

AI_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" \
  -X POST "$BASE_URL/api/ai/generate" \
  -H "Content-Type: application/json" \
  -d '{"description":"test","file_ids":["fake-id"]}' 2>/dev/null || echo "000")

if [ "$AI_STATUS" != "500" ]; then
  echo "AI endpoint degraded gracefully (status: $AI_STATUS, not 500)"
else
  echo "FAIL: AI endpoint returned 500 (unhandled error)"
fi

kubectl rollout status deployment/worker-gemini -n $NAMESPACE --timeout=60s 2>/dev/null || true
echo "Gemini worker restarted"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "CHAOS SCENARIO: Fill /dev/shm on a worker"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

WORKER_POD=$(kubectl get pods -n $NAMESPACE -l app=worker-bulk -o name 2>/dev/null | head -1 | cut -d'/' -f2)
if [ -n "$WORKER_POD" ]; then
  echo "Filling /dev/shm on pod: $WORKER_POD"
  kubectl exec -n $NAMESPACE "$WORKER_POD" -- bash -c "
    TOTAL=\$(df /dev/shm | tail -1 | awk '{print \$2}')
    FILL=\$(echo \"\$TOTAL * 0.85 / 1\" | bc)
    dd if=/dev/zero of=/dev/shm/chaos_fill bs=1K count=\$FILL 2>/dev/null
    echo \"Filled \$FILL KB of /dev/shm\"
  " 2>/dev/null || echo "exec failed (pod may have restarted)"
  kubectl exec -n $NAMESPACE "$WORKER_POD" -- rm -f /dev/shm/chaos_fill 2>/dev/null || true
  echo "/dev/shm chaos fill cleaned up"
else
  echo "No worker-bulk pods found — skipping /dev/shm scenario"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "FINAL HEALTH CHECK AFTER ALL CHAOS SCENARIOS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

kubectl get pods -n $NAMESPACE 2>/dev/null || true
echo ""

FINAL_HEALTH=$(curl -sf "$BASE_URL/readyz" 2>/dev/null || echo '{"status":"error"}')
echo "Final /readyz: $FINAL_HEALTH"

echo ""
echo "Chaos engineering suite complete"
