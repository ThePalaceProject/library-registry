# Library Registry

A discovery service for matching people to the libraries that serve them.

[![Test Library Registry & Build Docker Image](https://github.com/ThePalaceProject/library-registry/actions/workflows/test-build.yml/badge.svg)](https://github.com/ThePalaceProject/library-registry/actions/workflows/test-build.yml)

This is a [LYRASIS](http://lyrasis.org)-maintained fork of the NYPL [Library Simplified](http://www.librarysimplified.org/) Library Registry.

Docker images ar available at:
- https://github.com/orgs/ThePalaceProject/packages?repo_name=library-registry

## Cloning the Library Registry Repositories

You will need both this repository and the separate front end repo, in 
order to build the local development images. The registry front end repo
should be checked out into a directory named `registry_admin` in the same
parent directory as the `library-registry` repo itself. If it is not, you
will need to change the host mount instructions in the `docker-compose.yml`
file to accommodate its location. To get them both in the same directory, 
execute the following from that directory:

```shell
git clone https://github.com/thepalaceproject/library-registry.git
git clone https://github.com/thepalaceproject/library-registry-admin.git
```

## Key Environment Variables

These environment variables are generally applicable, regardless of installation method, and are included here because they are not discussed elsewhere in this document.

- EMAILER_RECIPIENT_OVERRIDE: If set, `emailer` will send all non-test email to this email address.

## Installation (Docker)

If not using Docker, skip to section entitled ["Installation (non-Docker)"](#installation-non-docker)

Because the Registry runs in a Docker container, the only required software is [Docker Desktop](https://www.docker.com/products/docker-desktop). The database and webapp containers expect to be able to operate on ports 5432 and 80, respectively--if those ports are in use already you may need to amend the `docker-compose.yml` file to add alternate ports.

_Note: If you would like to use the `Makefile` commands you will also need `make` in your `PATH`. They're purely convenience methods, so it isn't strictly required. If you don't want to use them just run the commands from the corresponding task in the `Makefile` manually. You can run `make help` to see the full list of commands._

While they won't need to be changed often, there are a couple of environment
variables set in the `Dockerfile` that are referenced within the container:
- `LIBRARY_REGISTRY_DOCKER_HOME` is the app directory.
- `LIBRARY_REGISTRY_DOCKER_VENV` is the app's virtual environment subdirectory
  name. It is computed based on the value of `LIBRARY_REGISTRY_DOCKER_HOME`.
  For more details, see:
  - https://github.com/pypa/pipenv/issues/1226#issuecomment-598487793
- `WORKON_HOME` is base directory for virtual environments.
- The effective virtual environment directory for the app will be `$WORKON_HOME/$LIBRARY_REGISTRY_DOCKER_VENV`.

### Building the Images

Local development uses two Docker images and one persistent Docker volume (for the PostgreSQL data directory). To create the base images:

```shell
cd library-registry
make build
```

## Usage

### Running the Containers

You can start up the local compose cluster in the background with:

```shell
make up
```

Alternatively, if you want to keep a terminal attached to the running containers, so you can see their output, use:

```shell
make up-watch
```

### Controlling the Cluster

- `make stop` to stop (but not remove) the running containers
- `make start` to restart a stopped cluster
- `make down` to stop and remove the running containers
- `make clean` to stop and remove the running containers and delete the database container's data volume

### Accessing the Containers

While the cluster is running, you can access the containers with these commands:

- `make db-session` - Starts a `psql` session on the database container as the superuser
- `make webapp-shell` - Open a shell on the webapp container

### Viewing the Web Interface

The Library Registry listens (via Nginx) on port 80, so once the cluster is running you should be able to point a browser at `http://localhost/admin/` and access it with the username/password `admin/admin`.

The [Library Registry Admin](git clone https://github.com/thepalaceproject/library-registry-admin.git)
front end is implemented as a Node package. The name and version of this package are configured in
`admin/config.py`. In addition, either or both may be overridden via environment variables. For example:
```shell
TPP_LIBRARY_REGISTRY_ADMIN_PACKAGE_NAME=@thepalaceproject/library-registry-admin
TPP_LIBRARY_REGISTRY_ADMIN_PACKAGE_VERSION=1.0.0
```

#### Debugging/Development of the Web Interface

The default configuration will result in the admin client being served from a content delivery
network. To enable use of a local copy to support development/debugging, ensure that this
repo and that of the admin UI have the same parent directory and then perform the following
from the base of this repo:
- `(cd admin && npm link ../../library-registry-admin)`

This will link the admin UI project into the admin directory in a manner that is compatible with
both docker and non-containerized development. If the package is properly linked, admin UI assets
will be served from the linked package, rather than the CDN.

## Installation (non-Docker)

To install the registry locally, you'll need the following:

- PostgreSQL 12+
- PostGIS 3
- Python 3.6+ (3.9 is the build target for the Docker install)
- Appropriate system dependencies to build the Python dependencies, which may include:
  - `make` / `gcc` / `build-essential` (debian) / `build-base` (alpine) / XCode CLI Tools (mac)
  - Compression libs like `bzip2-dev`, `zlib-dev`, etc.
  - PostgreSQL development libs: `libpq`, `postgresql-dev`, etc., for [`psycopg2`](https://www.psycopg.org)
  - Image processing libs for [`Pillow`](https://pillow.readthedocs.io/en/stable/) such as `libjpeg-dev`

### Creating the Databases

With a running PostgreSQL/PostGIS installation, you can create the required test and dev databases by executing:

```SQL
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
```

The database configuration is exposed to the application via environment variables.
```SHELL
SIMPLIFIED_TEST_DATABASE=postgresql://simplified_test:simplified_test@localhost:5432/simplified_registry_test
SIMPLIFIED_PRODUCTION_DATABASE=postgresql://simplified:simplified@localhost:5432/simplified_registry_dev
```
For development work, you should create a `.env` file in the project directory that includes these variables
set to the appropriate values for your environment.


### Installing Python Dependencies

The project expects to use [`pipenv`](https://pypi.org/project/pipenv/) for dependency and virtualenv management, so first install that:

```shell
pip install pipenv
```

Having done so, you should be able to run the following in the project directory to install all dependencies:

```
pipenv install --dev
```

### Running the Registry

To start the registry inside the virtualenv that `pipenv` creates:

```shell
FLASK_APP=app.py pipenv run flask run
```

Pipenv should read in the local `.env` file and supply those database connection strings to the application, which will be run by the Flask development server.
