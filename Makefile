.PHONY: help build clean full-clean \
		up up-watch start stop down \
		db-session webapp-shell test test-x \
		active-build active-up active-up-watch active-test active-test-x active-down active-clean active-full-clean

.DEFAULT_GOAL := help

help:
	@echo "Usage: make [COMMAND]"
	@echo ""
	@echo "Commands:"
	@echo ""
	@echo "    help             - Display this help text"
	@echo ""
	@echo "  Local Development, Setup and Teardown:"
	@echo ""
	@echo "    build            - Build the libreg_webapp and libreg_local_db images"
	@echo "    clean            - Take down the local cluster and removes the db volume"
	@echo "    full-clean       - Take down the local cluster and remove containers, volumes, and images"
	@echo ""
	@echo "  Local Development, Cluster Control:"
	@echo ""
	@echo "    up               - Bring up the local cluster in detached mode"
	@echo "    up-watch         - Bring up the local cluster, remains attached"
	@echo "    start            - Start a stopped cluster"
	@echo "    stop             - Stop the cluster without removing containers"
	@echo "    down             - Take down the local cluster"
	@echo ""
	@echo "  Local Development, Interacting with Running Containers:"
	@echo ""
	@echo "    db-session       - Start a psql session as the superuser on the db container"
	@echo "    webapp-shell     - Open a shell on the webapp container"
	@echo "    test             - Run the python test suite on the webapp container"
	@echo "    test-x           - Run the python test suite, exit at first failure"
	@echo ""
	@echo "  CI/CD, building 'active' images for deployment:"
	@echo ""
	@echo "    active-build      - Build images based on the docker-compose-cicd.yml file"
	@echo "    active-up         - Bring up the cluster from the docker-compose-cicd.yml file"
	@echo "    active-up-watch   - Bring up the cluster from the cicd file, stay attached"
	@echo "    active-test       - Run the test suite on the active container"
	@echo "    active-test-x     - Run the test suite on the active container, exit on first failure"
	@echo "    active-down       - Stop the cluster from the cicd file"
	@echo "    active-clean      - Stop the 'active'/cicd cluster, remove containers and volumes"
	@echo "    active-full-clean - Stop the 'active'/cicd cluster, remove containers, volumes, and images"
	@echo ""

##############################################################################
# Setup and Teardown Recipes
##############################################################################

build:
	docker-compose build

clean:
	docker-compose down --volumes

full-clean:
	docker-compose down --volumes --rmi all

##############################################################################
# Cluster Control Recipes
##############################################################################

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

##############################################################################
# Interacting with Running Containers Recipes
##############################################################################

db-session:
	docker exec -it registry_db psql -U postgres

webapp-shell:
	docker exec -it registry_webapp /bin/sh

test:
	docker exec -it --env TESTING=1 registry_webapp pipenv run pytest tests

test-x:
	docker exec -it --env TESTING=1 registry_webapp pipenv run pytest -x tests

##############################################################################
# CI/CD, building 'active' images for deployment Recipes
##############################################################################

active-build:
	docker-compose -f docker-compose-cicd.yml build

active-up:
	docker-compose -f docker-compose-cicd.yml up -d

active-up-watch:
	docker-compose -f docker-compose-cicd.yml up

active-test:
	docker exec -it --env TESTING=1 registry_active_webapp pipenv run pytest tests

active-test-x:
	docker exec -it --env TESTING=1 registry_active_webapp pipenv run pytest -x tests

active-down:
	docker-compose -f docker-compose-cicd.yml down

active-clean:
	docker-compose -f docker-compose-cicd.yml down --volumes

active-full-clean:
	docker-compose -f docker-compose-cicd.yml down --volumes --rmi all