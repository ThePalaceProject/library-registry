##############################################################################
# This is a multi-stage Dockerfile with three targets:
#   * libreg_local_db
#   * webapp_dev
#   * webapp_prod
# 
# For background on multi-stage builds, see:
#
#   https://docs.docker.com/develop/develop-images/multistage-build/
#
##############################################################################


##############################################################################
# Build target: libreg_local_db
FROM postgis/postgis:12-3.1 AS libreg_local_db

ENV POSTGRES_PASSWORD="password"
ENV POSTGRES_USER="postgres"

COPY ./docker/postgis_init.sh /docker-entrypoint-initdb.d/postgis-init.sh

EXPOSE 5432
##############################################################################


##############################################################################
# Intermediate stage: builder
#
# This stage builds out the common pieces of the dev and prod images, and isn't
# meant to be used as a build target. Though feel free--I'm a docstring, not a
# cop. It does the following:
#
#  * Installs Nginx from source, mirroring the process used in the official
#    Nginx Docker images.
#  * Via the system pip, installs:
#      * pipenv
#      * supervisor
#  * Copies in the config files for Gunicorn, Nginx, and Supervisor
#  * Sets the container entrypoint, which is a script that starts Supervisor

FROM python:3.9.2-alpine3.13 AS builder

EXPOSE 80

##### Install NGINX, and Supervisor (Gunicorn installed in virtualenv) #####
# This is a simplified version of the offical Nginx Dockerfile for Alpine 3.13:
# https://github.com/nginxinc/docker-nginx/blob/dcaaf66e4464037b1a887541f39acf8182233ab8/mainline/alpine/Dockerfile
ENV NGINX_VERSION 1.19.8
ENV NJS_VERSION   0.5.2
ENV PKG_RELEASE   1
ENV SUPERVISOR_VERSION 4.2.2
ENV SIMPLYE_RUN_WEBPACK_WATCH 0

RUN set -x \
    && addgroup -g 101 -S nginx \
    && adduser -S -D -H -u 101 -h /var/cache/nginx -s /sbin/nologin -G nginx -g nginx nginx \
    && nginxPackages=" \
        nginx=${NGINX_VERSION}-r${PKG_RELEASE} \
        nginx-module-xslt=${NGINX_VERSION}-r${PKG_RELEASE} \
        nginx-module-geoip=${NGINX_VERSION}-r${PKG_RELEASE} \
        nginx-module-image-filter=${NGINX_VERSION}-r${PKG_RELEASE} \
        nginx-module-njs=${NGINX_VERSION}.${NJS_VERSION}-r${PKG_RELEASE} \
    " \
    && KEY_SHA512="e7fa8303923d9b95db37a77ad46c68fd4755ff935d0a534d26eba83de193c76166c68bfe7f65471bf8881004ef4aa6df3e34689c305662750c0172fca5d8552a *stdin" \
    && apk add --no-cache --virtual .cert-deps openssl \
    && wget -O /tmp/nginx_signing.rsa.pub https://nginx.org/keys/nginx_signing.rsa.pub \
    && if [ "$(openssl rsa -pubin -in /tmp/nginx_signing.rsa.pub -text -noout | openssl sha512 -r)" = "$KEY_SHA512" ]; then \
        echo "key verification succeeded!"; \
        mv /tmp/nginx_signing.rsa.pub /etc/apk/keys/; \
    else \
        echo "key verification failed!"; \
        exit 1; \
    fi \
    && apk del .cert-deps \
    && apk add -X "https://nginx.org/packages/mainline/alpine/v$(egrep -o '^[0-9]+\.[0-9]+' /etc/alpine-release)/main" --no-cache $nginxPackages \
    && if [ -n "$tempDir" ]; then rm -rf "$tempDir"; fi \
    && if [ -n "/etc/apk/keys/abuild-key.rsa.pub" ]; then rm -f /etc/apk/keys/abuild-key.rsa.pub; fi \
    && if [ -n "/etc/apk/keys/nginx_signing.rsa.pub" ]; then rm -f /etc/apk/keys/nginx_signing.rsa.pub; fi \
    && apk add --no-cache --virtual .gettext gettext \
    && mv /usr/bin/envsubst /tmp/ \
    \
    && runDeps="$( \
        scanelf --needed --nobanner /tmp/envsubst \
            | awk '{ gsub(/,/, "\nso:", $2); print "so:" $2 }' \
            | sort -u \
            | xargs -r apk info --installed \
            | sort -u \
    )" \
    && apk add --no-cache $runDeps \
    && apk del .gettext \
    && mv /tmp/envsubst /usr/local/bin/ \
    && apk add --no-cache tzdata \
    && apk add --no-cache curl ca-certificates \
    && pip install \
           supervisor \
           pipenv \
    && mkdir /etc/gunicorn \
    && chown nginx:nginx /etc/gunicorn \
    && mkdir /var/log/supervisord \
    && chown nginx:nginx /var/log/supervisord
    
##### Set up Gunicorn, Nginx, and Supervisor configurations #####

# This causes pipenv not to spam the build output with extra lines when 
# running `pipenv install`:
#   https://github.com/pypa/pipenv/issues/4052#issuecomment-588480867
ENV CI 1

# This creates the /simplye_app directory without issuing a separate RUN directive.
WORKDIR /simplye_app

# Copy over the dependency files individually. We copy over the entire local
# directory later in the process, *after* the heavy RUN instructions, so that
# the docker layer caching isn't impacted by extraneous changes in the repo.
COPY ./Pipfile* ./

# Setting WORKON_HOME causes pipenv to put its virtualenv in a pre-determined, 
# OS-independent location. Note that /simplye_venv is NOT the virtualenv, it's 
# just the parent directory for virtualenvs created by pipenv. The actual venv
# is in a directory called something like
# /simplye_venv/simplye_app-QOci6oRN, which follows the pattern 
# '<name-of-project-dir>-<hashval>', where the hashval is deterministic and based on
# the path to the Pipfile. See this issue comment for a description of how that
# name is built:
#
#   https://github.com/pypa/pipenv/issues/1226#issuecomment-598487793
#
ENV WORKON_HOME /simplye_venv

# Install the system dependencies and the Python dependencies. Note that if 
# you want to be able to install new Python dependencies on the fly from
# within the container, you should remove the line below that deletes the
# build dependencies (`apk del --no-network .build-deps`), then rebuild
# the image.
RUN set -ex \
	&& apk add --no-cache --virtual .build-deps  \
		build-base \
		bzip2-dev \
		libffi-dev \
		libxslt-dev \
		npm \
		openssl-dev \
		postgresql-dev \
		zlib-dev \        
 # We need to leave these installed for psycopg2 and PIL
 && apk add --no-cache --virtual .runtime-deps \
    libpq \
    jpeg-dev \
    libxcb-dev \
 && mkdir ${WORKON_HOME} \
 && cd /simplye_app \
 && pipenv install --dev --skip-lock --clear \
 && apk del --no-network .build-deps

COPY ./docker/gunicorn.conf.py /etc/gunicorn/gunicorn.conf.py
COPY ./docker/nginx.conf /etc/nginx/nginx.conf
COPY ./docker/supervisord-alpine.ini /etc/supervisord.conf
COPY ./docker/runinvenv /usr/local/bin/runinvenv
COPY ./docker/webpack-watch /usr/local/bin/webpack-watch
COPY ./docker/docker-entrypoint.sh /docker-entrypoint.sh

ENTRYPOINT ["/bin/sh", "-c", "/docker-entrypoint.sh"]

##############################################################################


##############################################################################
# Build target: libreg_dev
# 
# Note that this target assumes a host mount is in place to link the current
# directory into the container at /simplye_app. The production target copies in
# the entire project directory since it will remain static.
FROM builder AS libreg_dev

ENV FLASK_ENV development
ENV SIMPLYE_RUN_WEBPACK_WATCH 1
ENV TESTING 1

RUN apk add --no-cache npm
##############################################################################


##############################################################################
# Build target: libreg_prod
#
FROM builder AS libreg_prod
ARG LIBRARY_REGISTRY_ADMIN_PACKAGE=@thepalaceproject/library-registry-admin

ENV FLASK_ENV production

# We'll be building the front end as static, so we need the package files.
COPY ./package*.json ./

# Compile the front end files and clean up the temporary build directory
RUN set -ex \
 && apk add --no-cache npm \
 && mkdir /tmp/simplye_npm_build \
 && cp ./package*.json /tmp/simplye_npm_build \
 && npm install --prefix /tmp/simplye_npm_build \
 && mkdir -p /simplye_static/static \
 && cp /tmp/simplye_npm_build/node_modules/${LIBRARY_REGISTRY_ADMIN_PACKAGE}/dist/* /simplye_static/static \
 && rm -rf /tmp/simplye_npm_build \
 && apk del npm

COPY . /simplye_app
##############################################################################
