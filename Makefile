SHELL := /bin/sh

IMAGE_NAME := astrobot
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
DATE := $(shell date -u +%Y%m%d)
IMAGE_TAG ?= $(GIT_SHA)

.PHONY: help
help:
	@echo "Targets:"
	@echo "  build         — build dev image (astrobot-app:latest)"
	@echo "  build-prod    — build prod image tagged with git SHA + date"
	@echo "  up            — start dev stack (postgres + redis + backup + app polling)"
	@echo "  down          — stop dev stack"
	@echo "  logs          — tail app logs"
	@echo "  migrate       — run alembic upgrade head"
	@echo "  test          — run pytest in container"
	@echo "  shell         — open shell in app container"
	@echo "  deploy-prod   — build prod image + push (set REGISTRY)"
	@echo ""
	@echo "Current IMAGE_TAG=$(IMAGE_TAG)"

.PHONY: build
build:
	docker compose build app

.PHONY: build-prod
build-prod:
	docker build \
		-t $(IMAGE_NAME):$(IMAGE_TAG) \
		-t $(IMAGE_NAME):$(DATE) \
		--build-arg GIT_SHA=$(GIT_SHA) \
		.

.PHONY: up
up:
	docker compose up -d

.PHONY: down
down:
	docker compose down

.PHONY: logs
logs:
	docker compose logs -f app

.PHONY: migrate
migrate:
	docker compose run --rm migrate

.PHONY: test
test:
	docker compose run --rm --no-deps app pytest -v

.PHONY: shell
shell:
	docker compose exec app sh

.PHONY: deploy-prod
deploy-prod: build-prod
ifndef REGISTRY
	$(error REGISTRY is not set, e.g. REGISTRY=registry.example.com/astrobot)
endif
	docker tag $(IMAGE_NAME):$(IMAGE_TAG) $(REGISTRY):$(IMAGE_TAG)
	docker push $(REGISTRY):$(IMAGE_TAG)
	@echo "Pushed $(REGISTRY):$(IMAGE_TAG)"
	@echo "On prod host: IMAGE_TAG=$(IMAGE_TAG) docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d"
