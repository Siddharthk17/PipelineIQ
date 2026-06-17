# PipelineIQ Production Runbook

## Environment

**Platform:** Civo k3s (lightweight Kubernetes)  
**Cluster:** pipelineiq-prod  
**Namespace:** pipelineiq  
**Registry:** ghcr.io/siddharthk17/pipelineiq-backend  

### Service URLs

| Service | URL | Access |
|---|---|---|
| API | https://pipelineiq-api.onrender.com | Public (TLS) |
| Frontend | https://pipelineiq.vercel.app | Vercel (external) |
| MinIO Console | Internal | kubectl port-forward |
| Jaeger UI | Internal | kubectl port-forward |

### Infrastructure Services (ClusterIP)

| Service | Port | Purpose |
|---|---|---|
| postgres | 5432 | PostgreSQL 15 + pgvector |
| redis-broker | 6379 | Celery message broker |
| redis-pubsub | 6380 | SSE event pub/sub |
| redis-cache | 6381 | LRU cache (1GB maxmemory) |
| redis-yjs | 6382 | Yjs CRDT persistence |
| y-websocket | 1234 | Yjs WebSocket collaboration |
| minio | 9000/9001 | S3-compatible object storage |

### Application Services

| Service | Replicas | Notes |
|---|---|---|
| pipelineiq-api | 2 (HPA: 2-5) | FastAPI via Gunicorn/Uvicorn |
| sse-service | 1 | Dedicated SSE streaming endpoint |
| worker-critical | 1 | Critical priority queue, concurrency=2 |
| worker-default | 2 | Default queue, concurrency=4 |
| worker-bulk | 2 | Bulk processing queue, concurrency=2 |
| worker-gemini | 1 | Gemini AI queue, concurrency=1 |
| worker-streaming | 1 | Redpanda streaming queue, concurrency=4 |
| worker-beat | 1 | Celery Beat scheduler |
| y-websocket | 1 | Yjs WebSocket CRDT collaboration |

---

## Common Operations

### Check pod status

```bash
kubectl get pods -n pipelineiq -o wide
kubectl describe pod <pod-name> -n pipelineiq
```

### View API logs

```bash
kubectl logs -n pipelineiq -l app=pipelineiq-api --tail=100 -f
kubectl logs -n pipelineiq deployment/pipelineiq-api --tail=100
```

### View Celery worker logs

```bash
kubectl logs -n pipelineiq -l app=worker-default --tail=100
kubectl logs -n pipelineiq -l app=worker-bulk --tail=100
kubectl logs -n pipelineiq deployment/worker-beat --tail=100
```

### Access MinIO console locally

```bash
kubectl port-forward -n pipelineiq svc/minio 9001:9001 &
# Open http://localhost:9001 in browser
```

### Access Jaeger trace UI locally

```bash
kubectl port-forward -n pipelineiq svc/jaeger 16686:16686 &
# Open http://localhost:16686 in browser
```

### Run database migration manually

```bash
kubectl run migration --rm -it \
  --image=ghcr.io/siddharthk17/pipelineiq-backend:latest \
  --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"migration","image":"ghcr.io/siddharthk17/pipelineiq-backend:latest","command":["alembic","upgrade","head"],"workingDir":"/app","envFrom":[{"configMapRef":{"name":"pipelineiq-config"}},{"secretRef":{"name":"pipelineiq-secrets"}}]}]}}' \
  -n pipelineiq
```

### Exec into a running pod

```bash
kubectl exec -it -n pipelineiq deployment/pipelineiq-api -- /bin/bash
kubectl exec -it -n pipelineiq deployment/worker-default -- /bin/bash
```

### Scale API replicas

```bash
# Scale up during high load
kubectl scale deployment/pipelineiq-api -n pipelineiq --replicas=4

# Scale down
kubectl scale deployment/pipelineiq-api -n pipelineiq --replicas=2
```

### View resource usage

```bash
kubectl top pods -n pipelineiq
kubectl top nodes
kubectl get hpa -n pipelineiq
```

### Rollback to previous deployment

```bash
kubectl rollout undo deployment/pipelineiq-api -n pipelineiq
kubectl rollout status deployment/pipelineiq-api -n pipelineiq
```

### Force pod restart after secret rotation

```bash
kubectl rollout restart deployment/pipelineiq-api -n pipelineiq
kubectl rollout restart deployment/worker-default -n pipelineiq
kubectl rollout restart deployment/worker-bulk -n pipelineiq
kubectl rollout restart deployment/worker-gemini -n pipelineiq
kubectl rollout restart deployment/worker-beat -n pipelineiq
kubectl rollout restart deployment/sse-service -n pipelineiq
```

### Emergency: take the API offline

```bash
kubectl scale deployment/pipelineiq-api -n pipelineiq --replicas=0
```

### Check configuration

```bash
kubectl get configmap pipelineiq-config -n pipelineiq -o yaml
kubectl get secret pipelineiq-secrets -n pipelineiq -o jsonpath='{.data}' | python3 -c "import sys,json,base64; d=json.load(sys.stdin); [print(f'{k}={len(base64.b64decode(v))} chars') for k,v in d.items()]"
```

---

## Disaster Recovery

### PostgreSQL pod crashed

1. Check the PVC still exists: `kubectl get pvc -n pipelineiq`
2. If crashed pod is hung, force delete: `kubectl delete pod <pod-name> -n pipelineiq --grace-period=0 --force`
3. The Deployment controller will recreate the pod automatically
4. Verify DB recovered: `kubectl logs -n pipelineiq -l app=postgres --tail=50`
5. Verify API can connect: `curl -s https://pipelineiq-api.onrender.com/readyz | python3 -m json.tool`

### MinIO data corruption

1. Do NOT delete the PVC — it holds all uploaded files and pipeline outputs
2. Restart the MinIO pod: `kubectl rollout restart deployment/minio -n pipelineiq`
3. Verify health: `kubectl port-forward -n pipelineiq svc/minio 9000:9000` then `curl http://localhost:9000/minio/health/live`
4. If unrecoverable, restore from a MinIO backup (set up periodic `mc mirror`)

### Secret rotation

1. Update each secret value using GitHub Actions secrets (Settings → Secrets and variables → Actions)
2. Trigger a new deployment (push to main, or manually run the deploy workflow via workflow_dispatch)
3. The deploy workflow recreates secrets and restarts all workloads
4. Verify pods restart cleanly: `kubectl get pods -n pipelineiq`

### Node failure (Civo)

```bash
# Check node status
kubectl get nodes -o wide
kubectl describe node <node-name>

# If Civo node is unrecoverable:
civo kubernetes show pipelineiq-prod

# Create a replacement node via Civo dashboard:
# https://dashboard.civo.com/kubernetes

# Verify all pods redistribute
kubectl get pods -n pipelineiq -o wide
```

### Rolling restart of all application pods

```bash
kubectl rollout restart deployment -n pipelineiq
```

---

## Deployment Process

### Automated (normal flow)

Push to `main` branch triggers `.github/workflows/deploy.yml`:

1. `build-and-push`: Builds multi-stage Docker image, pushes to GHCR with SHA tag
2. `deploy`: Applies k8s manifests, updates image tags, waits for rollout, runs smoke tests

### Manual deployment

```bash
civo kubernetes config pipelineiq-prod --save
kubectl config use-context pipelineiq-prod

kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml

kubectl create secret generic pipelineiq-secrets \
  --namespace=pipelineiq \
  --from-env-file=.env.production

kubectl apply -f k8s/postgres/
kubectl apply -f k8s/redis/
kubectl wait --for=condition=Available deployment/postgres -n pipelineiq --timeout=120s
kubectl apply -f k8s/minio/
kubectl apply -f k8s/backend/
kubectl apply -f k8s/workers/
kubectl apply -f k8s/ingress/

kubectl get pods -n pipelineiq
```

### Verify deployment

```bash
curl -sf https://pipelineiq-api.onrender.com/healthz | python3 -m json.tool
curl -sf https://pipelineiq-api.onrender.com/readyz | python3 -m json.tool
kubectl get pods -n pipelineiq
```

---

## Health Probes

| Endpoint | Probe Type | What It Checks | Failure Impact |
|---|---|---|---|
| `GET /healthz` | Liveness | Process alive | Pod restart |
| `GET /livez` | Liveness | Process alive | Pod restart |
| `GET /readyz` | Readiness | DB + Redis + Storage | Traffic removed |
| `GET /health` | Readiness alias | Same as /readyz | N/A |
