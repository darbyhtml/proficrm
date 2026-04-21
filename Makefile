# Makefile — CRM ПРОФИ.
# Wave 0.2g (2026-04-20): оркестрация локального CI-like прогона.
#
# Цели:
#   make lint          — ruff + black --check (быстро, на каждый commit)
#   make format        — ruff --fix + black (применить автофиксы)
#   make mypy          — прогон mypy + сравнение с baseline (ratcheting)
#   make bandit        — security scan (medium + high)
#   make test          — полный прогон Django test suite
#   make coverage      — прогон с coverage + HTML/XML отчёт
#   make ci            — полная CI-lite цепочка (всё выше)
#   make build-js      — минификация operator-panel.js + widget.js
#   make precommit     — прогон всех pre-commit hooks
#   make precommit-install — установка pre-commit в git hooks
#   make clean         — удалить cache, .pyc, mypy_cache
#   make help          — эта справка
#
# Предусловия:
# - Python 3.13 + venv (или контейнер со всеми deps)
# - requirements.txt + requirements-dev.txt установлены
# - docker compose up -d db redis (для тестов)

.PHONY: help lint format mypy bandit test coverage ci build-js precommit \
        precommit-install clean

SHELL := /bin/bash
BACKEND := backend
COVERAGE_HTML := docs/audit/coverage-baseline-html
MIN_JS := backend/static/ui

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# -----------------------------------------------------------------------------
# Lint / Format
# -----------------------------------------------------------------------------

lint: ## Run ruff + black --check (fast, on every commit)
	@echo "▶ ruff check..."
	@ruff check $(BACKEND)
	@echo "▶ black --check..."
	@black --check $(BACKEND)

format: ## Apply ruff --fix + black (autofixes)
	@echo "▶ ruff --fix..."
	@ruff check $(BACKEND) --fix
	@echo "▶ black..."
	@black $(BACKEND)
	@echo "✅ Formatted."

# -----------------------------------------------------------------------------
# Mypy (ratcheting)
# -----------------------------------------------------------------------------

mypy: ## Run mypy + compare to baseline (ratchet)
	@echo "▶ mypy ratchet..."
	@python scripts/mypy_ratchet.py

mypy-update: ## Update mypy baseline (after fixing a batch)
	@python scripts/mypy_ratchet.py --update

# -----------------------------------------------------------------------------
# Security
# -----------------------------------------------------------------------------

bandit: ## Run bandit -ll (medium + high)
	@echo "▶ bandit..."
	@bandit -c pyproject.toml -r $(BACKEND) -ll

pip-audit: ## Check known CVEs in production dependencies
	@echo "▶ pip-audit..."
	@cd $(BACKEND) && pip-audit -r requirements.txt --desc --format columns

# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------

test: ## Run full Django test suite
	@echo "▶ Django tests..."
	@cd $(BACKEND) && DJANGO_SETTINGS_MODULE=crm.settings_test python manage.py test --verbosity=1

coverage: ## Run tests with coverage + report
	@echo "▶ coverage run..."
	@cd $(BACKEND) && DJANGO_SETTINGS_MODULE=crm.settings_test \
		coverage run --source='.' --omit='*/migrations/*,*/tests*.py,*/test_*.py,*/conftest*.py,manage.py,crm/asgi.py,crm/wsgi.py' \
		manage.py test --verbosity=1
	@echo "▶ coverage report..."
	@cd $(BACKEND) && coverage report --skip-empty
	@cd $(BACKEND) && coverage html -d ../$(COVERAGE_HTML)
	@cd $(BACKEND) && coverage xml -o ../docs/audit/coverage-baseline.xml
	@echo "✅ HTML report: $(COVERAGE_HTML)/index.html"

# -----------------------------------------------------------------------------
# CI-lite (локальный прогон полного CI)
# -----------------------------------------------------------------------------

ci: lint mypy bandit coverage ## Full CI-lite (lint + mypy + bandit + coverage)
	@echo ""
	@echo "✅ All CI checks passed."

# -----------------------------------------------------------------------------
# JS minification (Wave 0.2h — quick win для 210 KB dead weight)
# -----------------------------------------------------------------------------

smoke-staging: ## MANDATORY end-of-session check — staging external reachability
	bash tests/smoke/staging_post_deploy.sh

smoke-prod: ## Prod post-deploy smoke (для gated deploys по release tag)
	bash tests/smoke/prod_post_deploy.sh

build-js: ## Minify operator-panel.js + widget.js via esbuild (npx — без глобальной установки)
	@echo "▶ esbuild operator-panel..."
	@npx --yes esbuild@0.25.9 backend/messenger/static/messenger/operator-panel.js \
		--minify --sourcemap --target=es2020 \
		--outfile=backend/messenger/static/messenger/operator-panel.min.js
	@echo "▶ esbuild widget..."
	@npx --yes esbuild@0.25.9 backend/messenger/static/messenger/widget.js \
		--minify --sourcemap --target=es2020 \
		--outfile=backend/messenger/static/messenger/widget.min.js
	@echo "✅ JS minified. Sizes:"
	@ls -lh backend/messenger/static/messenger/operator-panel*.js \
		backend/messenger/static/messenger/widget*.js 2>/dev/null || true

# -----------------------------------------------------------------------------
# Pre-commit
# -----------------------------------------------------------------------------

precommit: ## Run all pre-commit hooks on all files
	@pre-commit run --all-files

precommit-install: ## Install pre-commit into git hooks
	@pre-commit install
	@pre-commit install --hook-type pre-push
	@echo "✅ Pre-commit hooks installed (commit + push)."

# -----------------------------------------------------------------------------
# Maintenance
# -----------------------------------------------------------------------------

clean: ## Remove cache, .pyc, mypy_cache
	@find . -type d -name __pycache__ -not -path '*/node_modules/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete
	@rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov
	@echo "✅ Cleaned."
