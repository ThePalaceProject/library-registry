# Direct Installation of the Library Registry

The Library Registry is a WSGI-compliant Python/Flask web application which relies on a PostgreSQL database running the PostGIS geographic extension(s). It is served by the `gunicorn` WSGI server, behind an Nginx reverse proxy. Though the Docker containers are based on Alpine Linux, the Registry should be installable on most modern operating systems.

The instructions below assume familiarity with your system's package management, and the ability to troubleshoot issues that may arise while building software from source. Installing the Registry [via Docker containers](./Development.md) is the recommended path, so this is only a loose guide (and heavily informed by the [`Dockerfile`](../Dockerfile)).

## System-Level Dependencies

### Build Dependencies

During the install process, you are likely to need your system's equivalent of the following packages, plus those listed below under 'Runtime Dependencies'. The build dependencies may safely be removed after installation.

* `build-base` / `build-essential`
* `bzip2-dev`
* `libffi-dev`
* `libxslt-dev`
* `npm`
* `openssl-dev`
* `postgresql-dev`
* `zlib-dev`
* `make`
* `python2` (required for building some very old npm packages)

### Runtime Dependencies

The following system packages should not be removed after installation is complete, as they are required by various parts of the application stack:

* `libpq`
* `jpeg-dev`
* `libxcb-dev`

## Backing Services

### Database

The Registry requires a PostgreSQL 12+ server, and the PostGIS extension at 3.1+. Once you install those, connect as your Postgres admin user, and execute the SQL statements in the [`postgis_init.sh`](../docker/postgis_init.sh) file. This will create the databases and users the application needs, and install the correct extensions.

### Reverse Proxy Server

To proxy incoming requests to the `gunicorn` WSGI server, you will need to install Nginx 1.19+. Use a modified version of the [`nginx.conf`](../docker/nginx.conf) file to route requests to the WSGI server.

## Python Environment

The Registry runs on Python 3.12, though it is likely compatible with earlier Python3 versions as well. Once you install Python, you'll be able to set up a virtual environment to install Python dependencies into.

### Virtual Environment

Python package management for the Registry is via [`pipenv`](https://pipenv-fork.readthedocs.io/en/latest/), which can be installed with `python3 -m pip install pipenv`.

### Python Dependencies

To create the project's virtual environment and install the Python dependencies to it, you can run

```shell
pipenv install
```

in the root of this repository.

## Admin Webapp

The administrative webapp served at `/admin` is a single-page JavaScript application, and is served directly by the Nginx proxy server. To build the application, run the following in the repository root:

```shell
npm install
```

In the resulting `node_modules` directory, the static assets for the front end will be contained in `simplified-registry-admin/dist`, and should be copied to a location where Nginx knows to find them. In the containerized application this is `/simplye_static`, but can be anywhere as long as the Nginx config file is amended to point to that location.

## Operating the Stack

In the containerized version of the Registry, process management is via [`supervisord`](http://supervisord.org), though you could control the various pieces as system services, or via a custom script. It may be helpful to look at the [`supervisord-alpine.ini`](../docker/supervisord-alpine.ini) configuration file, and adjust that for your local system.

Note that the supervisor configuration does not cover controlling the PostgreSQL server, which you will need to manage separately.
