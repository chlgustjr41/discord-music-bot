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
	cd services/token-minter && python -m pytest tests/ -q

lint: ## Ruff over all services
	ruff check services/bot services/guardian services/token-minter

build: ## Build all images without starting
	$(COMPOSE) build

deploy: ## Production deploy (run on the VM)
	git pull origin master && $(COMPOSE) up -d --build

reauth: ## YouTube OAuth device flow for the v2 stack (playbook F2)
	./scripts/reauth-v2.sh

rollback-legacy: ## EMERGENCY (soak week only): stop v2, restart the stopped legacy containers
	$(COMPOSE) down
	docker start jacky-bot jacky-lavalink
	docker update --restart=unless-stopped jacky-bot jacky-lavalink
	@echo "Legacy stack restored; v2 is down. Queues/state are shared via Firestore."
