COMPOSE := docker compose -f deploy/docker-compose.yml --env-file deploy/.env

.PHONY: help up down restart logs ps test lint build deploy reauth

help: ## List commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

up: ## Start the stack
	$(COMPOSE) up -d --build

down: ## Stop the stack
	$(COMPOSE) down

restart: ## Restart one service: make restart s=lavalink
	$(COMPOSE) restart $(s)

logs: ## Tail logs (all, or one: make logs s=bot)
	$(COMPOSE) logs -f --tail=100 $(s)

ps: ## Show service status
	$(COMPOSE) ps

test: ## Run all unit tests
	cd services/bot && python -m pytest tests/ -q
	cd services/guardian && python -m pytest tests/ -q

lint: ## Ruff over both services
	ruff check services/bot services/guardian

build: ## Build all images without starting
	$(COMPOSE) build

deploy: ## Production deploy (run on the VM)
	git pull origin master && $(COMPOSE) up -d --build

reauth: ## YouTube OAuth device flow (legacy stack script; v2 flow lands in M2)
	./scripts/reauth-youtube.sh
