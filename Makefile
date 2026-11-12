.PHONY: help check check-mcp install install-mcp dev-install test clean run docker-build docker-up docker-down docker-logs

PYTHON ?= python
MCP_PYTHON ?= python

help: ## Affiche cette aide
	@echo "Deux environnements Python distincts (leurs dependances sont incompatibles, C81) :"
	@echo "  API : make dev-install PYTHON=<python du venv API>"
	@echo "  MCP : make install-mcp MCP_PYTHON=<python du venv MCP>"
	@echo "Ne jamais melanger les deux dans un meme venv : '.[mcp]' reintroduirait browser-use."
	@echo "'make check' verifie l'API ; 'make check-mcp' verifie le serveur MCP."
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

check: ## Verification API : ruff (F821,F811,E9) puis pytest hors tests live
	$(PYTHON) -m ruff check --select F821,F811,E9 .
	$(PYTHON) -m pytest tests -q -m "not live"

check-mcp: ## Verification MCP : le serveur s'importe sans app/ et ses tests passent
	$(MCP_PYTHON) -m pytest tests/unit/test_mcp_server.py -q

install-mcp: ## Installe l'environnement du serveur MCP depuis son verrou
	$(MCP_PYTHON) -m pip install -r requirements-mcp.lock pytest pytest-asyncio

install: ## Installe l'API depuis son verrou
	$(PYTHON) -m pip install -r requirements.lock
	$(PYTHON) -m pip install --no-deps -e .
	$(PYTHON) -m playwright install chromium

dev-install: install ## Installe l'API et les outils de developpement
	$(PYTHON) -m pip install -e ".[dev]"

test: ## Lance les tests
	pytest tests/ -v

test-cov: ## Lance les tests avec couverture
	pytest tests/ -v --cov=app --cov-report=html --cov-report=term

clean: ## Nettoie les fichiers temporaires
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/ .pytest_cache/ htmlcov/ .coverage

run: ## Lance le service en local
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

docker-build: ## Construit l'image Docker
	docker-compose build

docker-up: ## Lance le service avec Docker
	docker-compose up -d

docker-down: ## Arrête le service Docker
	docker-compose down

docker-logs: ## Affiche les logs Docker
	docker-compose logs -f

docker-restart: ## Redémarre le service Docker
	docker-compose restart

docker-shell: ## Ouvre un shell dans le conteneur
	docker-compose exec webtools /bin/bash

lint: ## Vérifie le code avec ruff
	ruff check app/ tests/

format: ## Formate le code avec ruff
	ruff format app/ tests/
