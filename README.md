# SimplyE Library Registry

A geographic search engine for matching people to the libraries that serve them.

## Cloning the Registry Repositories

You will need both this repository and the separate front end repo in order to build the local development images. The registry front end repo must be checked out to the same directory as the `library_registry` repo itself, or you will need to change the host mount instructions in the `docker-compose.yml` file to accomodate its location. To get them both in the same parent directory, just execute:

```shell
git clone https://github.com/NYPL-Simplified/library_registry.git
git clone https://github.com/NYPL-Simplified/registry_admin.git
```

## Installation (Docker)

If not using Docker, skip to section entitled ["Installation (non-Docker)"](#installation-non-docker)

Because the Registry runs in a Docker container, the only required software is [Docker Desktop](https://www.docker.com/products/docker-desktop). The database and webapp containers expect to be able to operate on ports 5432 and 80, respectively--if those ports are in use already you may need to amend the `docker-compose.yml` file to add alternate ports.

_Note: If you would like to use the `Makefile` commands you will also need `make` in your `PATH`. They're purely convenience methods, so it isn't strictly required. If you don't want to use them just run the commands from the corresponding task in the `Makefile` manually. You can run `make help` to see the full list of commands._

### Building the Images

Local development uses two Docker images and one persistent Docker volume (for the PostgreSQL data directory). To create the base images:

```shell
cd library_registry
make build
```

## Usage

### Running the Containers

You can start up the local compose cluster in the background with:

```shell
make up
```

Alternatively, if you want to keep a terminal attached to the running containers so you can see their output, use:

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

The registry listens (via Nginx) on port 80, so once the cluster is running you should be able to point a browser at `http://localhost/admin/` and access it with the username/password `admin/admin`.

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

You should then create a `.env` file in the project directory with the following contents:

```SHELL
SIMPLIFIED_TEST_DATABASE=postgresql://simplified_test:simplified_test@localhost:5432/simplified_registry_test
SIMPLIFIED_PRODUCTION_DATABASE=postgresql://simplified:simplified@localhost:5432/simplified_registry_dev
```

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

You can then navigate to http://localhost:5000/admin in the browser.

### Debugging

If you are served an error message on the admin home screen and you are running the app locally without Docker, you may need to run `npm install` in the root directory of the library_registry repo. You can also try running the same command in the root directory of the registry_admin repo.

The latter command will only work if the circulation-web and circulation repos are linked using `npm link`. To do this, run `npm link` in the registry_admin repo and then `npm link simplified-registry-admin` in the library_registry repo.

If you are using Docker, ensure it's running `npm install` for you by checking the configuration files.
