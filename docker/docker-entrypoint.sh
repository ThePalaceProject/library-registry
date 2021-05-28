#!/bin/sh

##############################################################################
# Wait for the database to be ready before starting the servers
##############################################################################

PG_READY=""
PG_READY_WAIT_SECONDS=5
COUNT=0
RETRIES=10

pg_is_ready () {
    pipenv run python > /dev/null 2>&1 <<EOF
import os,sys,psycopg2
try:
  psycopg2.connect(os.environ.get('SIMPLIFIED_PRODUCTION_DATABASE'))
except Exception:
  sys.exit(1)
sys.exit(0)
EOF
}

until [ -n "$PG_READY" ] || [ $COUNT -gt $RETRIES ]; do
    COUNT=$((COUNT+1))

    pg_is_ready

    if [ $? -eq 0 ]; then
        PG_READY="true"
    else
        echo "--- Postgres unavailable, sleeping $PG_READY_WAIT_SECONDS seconds"
        sleep $PG_READY_WAIT_SECONDS
    fi
done

##############################################################################
# Start the Supervisor process that manages Nginx and Gunicorn, with
# a webpack file watcher for the front end if we're in dev mode.
##############################################################################

if [ -n "$PG_READY" ]; then
    exec /usr/local/bin/supervisord -c /etc/supervisord.conf
else
    echo "Database never became available, exiting!"
    exit 1
fi