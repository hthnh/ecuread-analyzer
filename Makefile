.PHONY: install test train run docker-up docker-down

install:
	python -m pip install -e ".[dev]"

test:
	pytest

train:
	python scripts/train_model.py --input tests/fixtures/serial_log_20251105_204330.txt --sample-interval-ms 100

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000

docker-up:
	docker compose up --build

docker-down:
	docker compose down

