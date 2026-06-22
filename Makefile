PYTHON ?= uv run

.PHONY: test run worker lint compose-config

test:
	$(PYTHON) pytest -q

run:
	$(PYTHON) uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

worker:
	$(PYTHON) python -m app.workers.main

compose-config:
	docker compose config
