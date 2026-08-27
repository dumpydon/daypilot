PYTHON := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: install backend frontend dev test lint eval reset-demo

install:
	python3.13 -m venv .venv
	$(PIP) install -e '.[dev]'
	cd frontend && npm install

backend:
	$(PYTHON) -m uvicorn backend.app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

dev:
	./scripts/dev.sh

test:
	$(PYTHON) -m pytest
	cd frontend && npm test -- --run

lint:
	$(PYTHON) -m ruff check backend mcp_servers evaluation
	cd frontend && npm run lint && npm run typecheck

eval:
	$(PYTHON) -m evaluation.run_evals

reset-demo:
	$(PYTHON) -m mcp_servers.common.reset_demo

