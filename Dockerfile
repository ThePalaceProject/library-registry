##############################################################################
# This is a multi-stage Dockerfile with three targets:
#   * libreg_local_db
#   * libreg_local
#   * libreg_active
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
#  * Via the system pip, installs supervisor
#  * Installs poetry
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
ENV POETRY_VERSION 1.3.2
ENV POETRY_URL "https://install.python-poetry.org"
ENV POETRY_HOME "/etc/poetry"

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
    && pip install --upgrade pip \
    && apk add --no-cache tzdata \
    && apk add --no-cache curl ca-certificates \
    && apk add --no-cache --virtual .build-base build-base \
    && curl -sSL ${POETRY_URL} | python - \
    && apk del .build-base \
    && ln -s ${POETRY_HOME}/bin/poetry /bin/poetry \
    && pip install supervisor \
    && mkdir /etc/gunicorn \
    && chown nginx:nginx /etc/gunicorn \
    && mkdir /var/log/supervisord \
    && chown nginx:nginx /var/log/supervisord

##### Set up Gunicorn, Nginx, and Supervisor configurations #####
# `LIBRARY_REGISTRY_DOCKER_HOME` is the app's directory in the docker container.
ENV LIBRARY_REGISTRY_DOCKER_HOME=/apps/library-registry

WORKDIR $LIBRARY_REGISTRY_DOCKER_HOME

# Copy over the dependency files individually. We copy over the entire local
# directory later in the process, *after* the heavy RUN instructions, so that
# the docker layer caching isn't impacted by extraneous changes in the repo.
COPY pyproject.toml ./
COPY ./poetry.lock ./

# Tell poetry not to use a virtualenv since we are in a container
ENV POETRY_VIRTUALENVS_CREATE=false

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
		openssl-dev \
		postgresql-dev \
		zlib-dev \
 # We need to leave these installed for psycopg2 and PIL
 && apk add --no-cache --virtual .runtime-deps \
    libpq \
    jpeg-dev \
    libxcb-dev \
 && cd "${LIBRARY_REGISTRY_DOCKER_HOME}" \
 && poetry install --only main,pg \
 && poetry cache clear -n --all pypi \
 && apk del --no-network .build-deps

COPY ./docker/gunicorn.conf.py /etc/gunicorn/gunicorn.conf.py
COPY ./docker/nginx.conf /etc/nginx/nginx.conf
COPY ./docker/supervisord-alpine.ini /etc/supervisord.conf
COPY ./docker/docker-entrypoint.sh /docker-entrypoint.sh

ENTRYPOINT ["/bin/sh", "-c", "/docker-entrypoint.sh"]

##############################################################################


##############################################################################
# Build target: libreg_local
#
# Note that this target assumes a host mount is in place to link the current
# directory into the container at /simplye_app. The production target copies in
# the entire project directory since it will remain static.
FROM builder AS libreg_local

ENV FLASK_ENV development

# Install development dependancies with poetry
RUN set -ex \
 && apk add --no-cache --virtual .build-deps build-base \
 && poetry install --no-root -E pg \
 && poetry cache clear -n --all pypi \
 && cd "${LIBRARY_REGISTRY_DOCKER_HOME}" \
 && apk del --no-network .build-deps

##############################################################################


##############################################################################
# Build target: libreg_active
#
FROM builder AS libreg_active

ENV FLASK_ENV production

COPY . ./
##############################################################################
