# PipelineIQ operator shortcuts.
#
# Fresh VM?  `make setup && make up` is the entire deploy.

.PHONY: setup up down logs status verify

# Bootstrap .env/secrets/host config. Safe to re-run (fills gaps only).
setup:
	./scripts/bootstrap.sh

# If .env is missing, bootstrap first so `make up` works on a fresh clone.
.env:
	./scripts/bootstrap.sh

# Build images and start the full stack.
up: .env
	docker compose up -d --build
	@$(MAKE) --no-print-directory status

# Stop the stack (volumes/data preserved).
down:
	docker compose down

# Follow logs. Usage: make logs [SVC=api]
SVC ?=
logs:
	docker compose logs --tail=100 -f $(SVC)

# Container + public endpoint status.
status:
	docker compose ps --format "table {{.Name}}\t{{.Status}}"
	@echo "--- public endpoints ---"
	@for p in "/" "/docs" "/openapi.json" "/redoc" "/flower/" "/grafana/login" "/jaeger/" "/prometheus/graph" "/health" "/readyz"; do \
		printf "%-18s " "$$p"; curl -s -o /dev/null -w "%{http_code}\n" "http://localhost$$p"; \
	done

# Wait for a healthy API, then prove auth + key routes end to end.
verify:
	@echo "Waiting for a healthy API (up to ~4 min)..."
	@for i in $$(seq 1 48); do curl -sf http://localhost/health >/dev/null 2>&1 && break || sleep 5; done
	@curl -sf http://localhost/health | head -c 200; echo
	@TOKEN=$$(curl -s -X POST http://localhost/auth/login -H 'Content-Type: application/json' -d '{"email":"demo@pipelineiq.app","password":"Demo1234!"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])"); \
	test -n "$$TOKEN" && echo "login: OK" || (echo "login: FAILED"; exit 1); \
	for p in "/" "/docs" "/openapi.json" "/redoc" "/flower/" "/grafana/login" "/jaeger/" "/prometheus/graph"; do \
		printf "%-18s " "$$p"; curl -s -o /dev/null -w "%{http_code}\n" "http://localhost$$p"; \
	done; \
	unset TOKEN
	@echo "verify: done (401 on /flower/ and 302s are the healthy answers)"
