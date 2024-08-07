#!/bin/sh

##############################################################################
# Wait for the database to be ready
##############################################################################

DB_READY=""
DB_READY_WAIT_SECONDS=5
COUNT=0
RETRIES=10

db_is_ready () {
    "$(pipenv --venv)/bin/python" > /dev/null 2>&1 <<EOF
import os,sys,psycopg2
try:
  psycopg2.connect(os.environ.get('SIMPLIFIED_PRODUCTION_DATABASE'))
except Exception:
  sys.exit(1)
sys.exit(0)
EOF
}

until [ -n "$DB_READY" ] || [ $COUNT -gt $RETRIES ]; do
    COUNT=$((COUNT+1))

    db_is_ready

    if [ $? -eq 0 ]; then
        DB_READY="true"
    else
        echo "--- Database unavailable, sleeping $DB_READY_WAIT_SECONDS seconds"
        sleep $DB_READY_WAIT_SECONDS
    fi
done

##############################################################################
# Start the Supervisor process that manages Nginx and Gunicorn.
##############################################################################

if [ -n "$DB_READY" ]; then
    exec /usr/local/bin/supervisord -c /etc/supervisord.conf
else
    echo "Database never became available, exiting!"
    exit 1
fi