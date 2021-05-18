#!/bin/sh

##############################################################################
# Set up the Library Registry Admin front end if we're in development mode
##############################################################################

# If this is the production image, the static copies of the registry admin
# files are already present, and we don't need to do any symlinking.
if [ ! -d /simplye_static/static ]; then
    echo "No static files directory found, building registry_admin frontend"
    if [ ! -d /registry_admin ]; then
        echo "Nothing found at /registry_admin--is that repo host mounted into the container?"
        exit 1
    fi

    # Establish that this is a local install
    cd /registry_admin && npm link

    # Make the registry link to the local install version of the admin
    cd /simplye_app && npm link library-registry-admin
    echo "NPM link completed: rc=$?"

    # Create a symlink in the location Nginx expects to serve static files from
    mkdir -p /simplye_static
    ln -s /registry_admin/dist /simplye_static/static
    echo "Static files..."
    ls /simplye_static/static
fi

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