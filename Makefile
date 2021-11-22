.PHONY: help install build db-session webapp-shell up up-watch start stop down test clean full-clean build-active up-active up-active-watch test-active down-active
.DEFAULT_GOAL := help

help:
	@echo "Usage: make [COMMAND]"
	@echo ""
	@echo "Commands:"
	@echo ""
	@echo "  Related to Local Development:"
	@echo ""
	@echo "    build            - Build the libreg_webapp and libreg_local_db images"
	@echo "    db-session       - Start a psql session as the superuser on the db container"
	@echo "    webapp-shell     - Open a shell on the webapp container"
	@echo "    up               - Bring up the local cluster in detached mode"
	@echo "    up-watch         - Bring up the local cluster, remains attached"
	@echo "    start            - Start a stopped cluster"
	@echo "    stop             - Stop the cluster without removing containers"
	@echo "    down             - Take down the local cluster"
	@echo "    test             - Run the python test suite on the webapp container"
	@echo "    test-x           - Run the python test suite, exit at first failure"
	@echo "    clean            - Take down the local cluster and removes the db volume"
	@echo "    full-clean       - Take down the local cluster and remove containers, volumes, and images"
	@echo ""
	@echo "  Related to Deployment:"
	@echo ""
	@echo "    build-active     - Build images based on the docker-compose-cicd.yml file"
	@echo "    up-active        - Bring up the cluster from the docker-compose-cicd.yml file"
	@echo "    up-active-watch  - Bring up the cluster from the cicd file, stay attached"
	@echo "    test-active      - Run the test suite on the active container"
	@echo "    test-active-x    - Run the test suite on the active container, exit on first failure"
	@echo "    down-active      - Stop the cluster from the cicd file"
	@echo ""

build:
	docker-compose build

db-session:
	docker exec -it registry_db psql -U postgres

webapp-shell:
	docker exec -it registry_webapp /bin/sh

up:
	docker-compose up -d

up-watch:
	docker-compose up

start:
	docker-compose start

stop:
	docker-compose stop

down:
	docker-compose down

test:
	docker exec -it --env TESTING=1 registry_webapp pipenv run pytest tests

test-x:
	docker exec -it --env TESTING=1 registry_webapp pipenv run pytest -x tests

clean:
	docker-compose down --volumes

full-clean:
	docker-compose down --volumes --rmi all

build-active:
	docker-compose -f docker-compose-cicd.yml build

up-active:
	docker-compose -f docker-compose-cicd.yml up -d

up-active-watch:
	docker-compose -f docker-compose-cicd.yml up

test-active:
	docker exec -it --env TESTING=1 registry_active_webapp pipenv run pytest tests

test-active-x:
	docker exec -it --env TESTING=1 registry_active_webapp pipenv run pytest -x tests

down-active:
	docker-compose -f docker-compose-cicd.yml down
