#!/bin/bash

set -e

psql -v ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE simplified_registry_dev;
    CREATE USER simplified WITH PASSWORD 'simplified';
    GRANT ALL PRIVILEGES ON DATABASE simplified_registry_dev TO simplified;

    CREATE DATABASE simplified_registry_test;
    CREATE USER simplified_test WITH PASSWORD 'simplified_test';
    GRANT ALL PRIVILEGES ON DATABASE simplified_registry_test TO simplified_test;

    \c simplified_registry_dev
    CREATE EXTENSION fuzzystrmatch;
    CREATE EXTENSION postgis;

    \c simplified_registry_test
    CREATE EXTENSION fuzzystrmatch;
    CREATE EXTENSION postgis;

    \c simplified_registry_dev postgres
    GRANT ALL ON SCHEMA public TO simplified;

    \c simplified_registry_test postgres
    GRANT ALL ON SCHEMA public TO simplified;

EOSQL
