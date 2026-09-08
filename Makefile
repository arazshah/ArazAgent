.PHONY: up down logs probe webhook webhook-delete password keys test fmt lint psql stats secrets-check

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f app

probe:
	python scripts/probe_bale.py

webhook:
	python scripts/set_webhook.py

webhook-delete:
	python scripts/set_webhook.py --delete

password:
	python scripts/set_admin_password.py

keys:
	@python -c "from cryptography.fernet import Fernet; print('SECRET_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
	@python -c "import secrets; print('SESSION_SECRET=' + secrets.token_urlsafe(48))"

test:
	pytest

fmt:
	ruff format .

lint:
	ruff check .
	mypy app

psql:
	docker compose exec db psql -U araz -d araz_agent

stats:
	python scripts/stats.py --recover

secrets-check:
	bash scripts/check_secrets.sh --all
