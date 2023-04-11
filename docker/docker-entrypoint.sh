#!/bin/sh

##############################################################################
# Wait for the database to be ready and migrated before starting the servers
##############################################################################

PG_READY=""
PG_READY_WAIT_SECONDS=5
COUNT=0
RETRIES=10
MIGRATION_COMPLETE=""

pg_is_ready () {
    python > /dev/null 2>&1 <<EOF
import os,sys,psycopg2
try:
  psycopg2.connect(os.environ.get('SIMPLIFIED_PRODUCTION_DATABASE'))
except Exception:
  sys.exit(1)
sys.exit(0)
EOF
}

migrate_db () {
    ./bin/migrate_database > /dev/null 2>&1
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

if  [ -n "$PG_READY" ]; then
    echo "--- Ready to begin database migration."
    migrate_db

    if [ $? -eq 0 ]; then
        MIGRATION_COMPLETE="true"
        echo "--- The database migration completed successfully."
    fi
fi
##############################################################################
# Start the Supervisor process that manages Nginx and Gunicorn, with
# a webpack file watcher for the front end if we're in dev mode.
##############################################################################

if [ -n "$PG_READY" ] && [ -n"$MIGRATION_COMPLETE" ]; then
    exec /usr/local/bin/supervisord -c /etc/supervisord.conf
else
    if [ -n "$PG_READY" ]; then
      echo "Database never became available, exiting!"
    else
      echo "Database migration did not complete successfully: exiting!"
    fi
    exit 1
fi
