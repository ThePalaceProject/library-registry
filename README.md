# Library Registry

A discovery service for matching people to the libraries that serve them.

[![Test Library Registry & Build Docker Image](https://github.com/ThePalaceProject/library-registry/actions/workflows/test-build.yml/badge.svg)](https://github.com/ThePalaceProject/library-registry/actions/workflows/test-build.yml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Imports: isort](https://img.shields.io/badge/%20imports-isort-%231674b1?style=flat&labelColor=ef8336)](https://pycqa.github.io/isort/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
![Python: 3.8,3.9,3.10,3.11](https://img.shields.io/badge/Python-3.8%20%7C%203.9%20%7C%203.10%20%7C%203.11-blue)

This is a [LYRASIS](http://lyrasis.org)-maintained fork of the NYPL
[Library Simplified](http://www.librarysimplified.org/) Library Registry.

Docker images are available at:

- [library-registry](https://github.com/orgs/ThePalaceProject/packages?repo_name=library-registry)

## Cloning the Library Registry Repositories

You will need both this repository and the separate front end repo, in order to build the local development images. The
registry front end repo should be checked out into a directory named `registry_admin` in the same parent directory as
the `library-registry` repo itself. If it is not, you will need to change the host mount instructions in the
`docker-compose.yml` file to accommodate its location. To get them both in the same directory, execute the following
from that directory:

```shell
git clone https://github.com/thepalaceproject/library-registry.git
git clone https://github.com/thepalaceproject/library-registry-admin.git
```

## Key Environment Variables

These environment variables are generally applicable, regardless of installation method, and are included here because
they are not discussed elsewhere in this document.

- EMAILER_RECIPIENT_OVERRIDE: If set, `emailer` will send all non-test email to this email address.

## AWS configuration setup for the storage

- SIMPLIFIED_AWS_S3_BUCKET_NAME (mandatory): The name of the bucket to use on S3
- SIMPLIFIED_AWS_S3_ENDPOINT_URL: The API endpoint for the S3 bucket

Of the above, only the `SIMPLIFIED_AWS_S3_BUCKET_NAME` is a mandatory configuration.
The underlying boto library will manage the credentials and authentication mechanism. [[source](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html)]

## Installation (Docker)

If not using Docker, skip to section entitled ["Installation (non-Docker)"](#installation-non-docker)

Because the Registry runs in a Docker container, the only required software is
[Docker Desktop](https://www.docker.com/products/docker-desktop). The database and webapp containers expect to be able
to operate on ports 5432 and 80, respectively--if those ports are in use already you may need to amend the
`docker-compose.yml` file to add alternate ports.

_Note: If you would like to use the `Makefile` commands you will also need `make` in your `PATH`. They're purely
convenience methods, so it isn't strictly required. If you don't want to use them just run the commands from the
corresponding task in the `Makefile` manually. You can run `make help` to see the full list of commands._

While they won't need to be changed often, there are a couple of environment variables set in the `Dockerfile` that are
referenced within the container:
- `LIBRARY_REGISTRY_DOCKER_HOME` is the app directory.

### Building the Images

Local development uses two Docker images and one persistent Docker volume (for the PostgreSQL data directory). To create
the base images:

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

The Library Registry listens (via Nginx) on port 80, so once the cluster is running you should be able to point a
browser at `http://localhost/admin/` and access it with the username/password `admin/admin`.

The [Library Registry Admin](https://github.com/thepalaceproject/library-registry-admin.git)
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
- Python 3.8+ (3.9 is the build target for the Docker install)
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

The project expects to use [`poetry`](https://python-poetry.org) for dependency and virtualenv management, so first
[install that](https://python-poetry.org/docs/#installation).

Having done so, you should be able to run the following in the project directory to install all dependencies.

For a development environment:

```shell
poetry install
```

For a production environment:

```shell
poetry install --only main,pg
```

### Running the Registry

To start the registry inside the virtualenv that `poetry` creates:

```shell
FLASK_APP=app.py poetry run flask run
```

## Continuous Integration

This project runs all the unit tests through Github Actions for new pull requests and when merging into the default
`main` branch. The relevant file can be found in `.github/workflows/test-build.yml`. When contributing updates or fixes,
it's required for the test Github Action to pass for all python environments. Run the `tox` command locally before
pushing changes to make sure you find any failing tests before committing them.

### Code Style

Code style on this project is linted using [pre-commit](https://pre-commit.com/). This python application is included
in our `pyproject.toml` file, so if you have the applications requirements installed it should be available. pre-commit
is run automatically on each push and PR by our [CI System](#continuous-integration).

You can run it manually on all files with the command: `pre-commit run --all-files`.

For more details about our code style, see the
[code style section of the circulation README](https://github.com/ThePalaceProject/circulation#code-style).

### Testing

Github Actions runs our unit tests against different Python versions automatically using
[tox](https://tox.readthedocs.io/en/latest/).

To run `pytest` unit tests locally, install `tox`.

```shell
poetry install --only ci
```

Tox has an environment for each python version and an optional `-docker` factor that will automatically use docker to
deploy service container used for the tests. You can select the environment you would like to test with the tox `-e`
flag.

#### Environments

| Environment | Python Version |
|-------------|----------------|
| py38        | Python 3.8     |
| py39        | Python 3.9     |
| py310       | Python 3.10    |
| py311       | Python 3.11    |

All of these environments are tested by default when running tox. To test one specific environment you can use the `-e`
flag.

Test Python 3.8

```shell
tox -e py38
```

You need to have the Python versions you are testing against installed on your local system. `tox` searches the system
for installed Python versions, but does not install new Python versions. If `tox` doesn't find the Python version its
looking for it will give an `InterpreterNotFound` errror.

[Pyenv](https://github.com/pyenv/pyenv) is a useful tool to install multiple Python versions, if you need to install
missing Python versions in your system for local testing.

#### Docker

If you install `tox-docker` tox will take care of setting up all the service containers necessary to run the unit tests
and pass the correct environment variables to configure the tests to use these services. Using `tox-docker` is not
required, but it is the recommended way to run the tests locally, since it runs the tests in the same way they are run
on Github Actions. `tox-docker` is installed automatically as part of the `ci` poetry group.

The docker functionality is included in a `docker` factor that can be added to the environment. To run an environment
with a particular factor you add it to the end of the environment.

Test with Python 3.8 using docker containers for the services.

```shell
tox -e py38-docker
```

#### Override `pytest` arguments

If you wish to pass additional arguments to `pytest` you can do so through `tox`. The default argument passed to `pytest`
is `tests`, however you can override this. Every argument passed after a `--` to the `tox` command line will the passed
to `pytest`, overriding the default.

Only run the `test_app.py` tests with Python 3.10 using docker.

```shell
tox -e py310-docker -- tests/test_app.py
```
