.PHONY: help check check-mcp install dev-install test clean run docker-build docker-up docker-down docker-logs

PYTHON ?= python

help: ## Affiche cette aide
	@echo "Deux environnements Python distincts (leurs dependances sont incompatibles) :"
	@echo "  API : pip install -e \".[dev]\"   (browser-use impose pydantic<2.11)"
	@echo "  MCP : pip install <liste de l'extra mcp de pyproject.toml>, SANS les dependances"
	@echo "        principales (un extra s'ajoute a elles : .[mcp] reintroduit browser-use)"
	@echo "Ne jamais melanger les deux dans un meme venv. 'make check' vise l'API ;"
	@echo "'make check-mcp' verifie seulement que la liste de l'extra mcp se resout seule."
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

check: ## Verification API : ruff (F821,F811,E9) puis pytest hors tests live
	$(PYTHON) -m ruff check --select F821,F811,E9 .
	$(PYTHON) -m pytest tests -q -m "not live"

check-mcp: ## Verifie que la liste de l'extra mcp se resout seule (dry-run, rien d'installe)
	$(PYTHON) -c "import tomllib; print('
'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['optional-dependencies']['mcp']))" > .mcp-requirements.tmp
	$(PYTHON) -m pip install --dry-run --ignore-installed -r .mcp-requirements.tmp; rc=$$?; rm -f .mcp-requirements.tmp; exit $$rc

install: ## Installe les dépendances
	pip install -e .
	playwright install chromium

dev-install: ## Installe les dépendances de développement
	pip install -e ".[dev]"
	playwright install chromium

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
	docker-compose exec webextract /bin/bash

lint: ## Vérifie le code avec ruff
	ruff check app/ tests/

format: ## Formate le code avec ruff
	ruff format app/ tests/
