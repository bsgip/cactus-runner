# Cactus Runner

Cactus Runner is a component of the Client CSIP-AUS Test Harness.

## Test Procedures

The test procedures are defined in the [cactus-test-definitions](https://github.com/bsgip/cactus-test-definitions) repository.

## API

| Endpoint | Query Parameters | Description |
| --- | --- | --- |
| `/finalize` | - | Ends a test procedure and returns a final summary of the test procedure as json. |
| `/start` | `test`, `lfdi` | Starts a test procedure. The test procedure is selected by the `test` parameter. The `lfdi` parameter is used to create the aggregator and associated certificate that the test client will use. |
| `/status` | - | Returns the status of the active test (if present) as json. |

For convenience an API client collection is provided for the [Bruno](https://www.usebruno.com/) application in the `bruno` directory.  See [here](#setup) for instructions setting up the bruno collection.

## Environment Variables

The cactus runner application uses the following environment variables,

| Environment Variable | Default Value | Description |
| --- | --- | --- |
| DATABASE_URL | - | The database connection string of an envoy database. |
| ENVOY_ADMIN_BASICAUTH_USERNAME | - | Username used for HTTP Basic Authentication when accessing the envoy-admin API.  |
| ENVOY_ADMIN_BASICAUTH_PASSWORD | - | Password used for HTTP Basic Authentication when accessing the envoy-admin API. Must be used in conjunction with ENVOY_ADMIN_BASICAUTH_USERNAME. |
| SERVER_URL | `http://localhost:8000` | The URL of an envoy server. |
| APP_HOST | `0.0.0.0` | The host IP of the cactus runner application. |
| APP_PORT | 8000 | The port the cactus runner application listens on. |
| ENVOY_ENV_FILE | `/shared/envoy.env` | The location to write the test-specific envoy environment variables. |
| DEV_SKIP_AUTHORIZATION_CHECK | "false" | If True ("true", "1", "t") no check is made that the forwarded certificate is valid. Intended for dev purposes only. |

> NOTE:
> The `DATABASE_URL` has no default value so it must be a defined. `postgresql+psycopg://test_user:test_pwd@localhost:8003/test_db` is suitable value to use with the envoy stack defined in the [docker-compose.yaml](https://github.com/bsgip/cactus-runner/blob/main/docker-compose.yaml).

> NOTE:
> There is another `DATABASE_URL` variable defined inside the [docker-compose.yaml](https://github.com/bsgip/cactus-runner/blob/main/docker-compose.yaml) file for use by other services in the docker stack. An important difference between the two database connection strings in the choice of driver. The docker-compose.yaml variable uses `asyncpg` whilst the cactus runner makes blocking calls the database using `psycopg`.

## Logging

Logging is configured in the `config/logging/config.json` file.

In the current configurtion, debug and info messages are written to `stdout`. Warning and error messages are written to `stderr`.

A persistent log is written to `logs/cactus_runner.jsonl`. All messages to the persistent log are written in a structured [JSONL](https://jsonlines.org/) format for easy (machine) searching/parsing.

## Dev

### Setup

The `cactus_runner` package (provided by this repo) should be installed in a suitable virtual environment. Activate your virtual environment and then run,

```sh
pip install --editable .[dev,test,cli]
```

This repo includes an API client collection made for [Bruno](https://www.usebruno.com/). Bruno is an open-source alternative to [Postman](https://www.postman.com/) and doesn't require an account to use. There are [multiple ways to install Bruno](https://www.usebruno.com/downloads). For linux users, the easiest way is via flatpak or snap,

```sh
flatpak install flathub com.usebruno.Bruno
```

or

```sh
sudo snap install bruno
```

Once Bruno is installed, we need to add the API client collection. Run Bruno, then choose *Collection → Open Collection* from the menu. Navigate to the project root directory, then the `bruno` directory. Then click the *Add* button.

A new collection called `Cactus-Runner` should appear in the right-hand bar. Clicking on the Cactus-Runner collection should reveal four requests (2 GET requests and 2 POST requests) that can be issued from Bruno.

Next we need to define the [environment variables](#environment-variables). The easiest way is to add then to a `.env` file, using the `dotenv` cli command,

```sh
dotenv set DATABASE_URL postgresql+psycopg://test_user:test_pwd@localhost:8003/test_db
```

Finally we need to create a docker image of the envoy server (tagged as `envoy:latest`) for the cactus runner to interact with. To build this image, follow [these instructions](https://github.com/bsgip/envoy/blob/main/demo/README.md).

### Running locally

Start the docker compose stack,

```
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d
```

Start the cactus-runner,

```
dotenv run -- python src/cactus_runner/app/main.py
```

Using Bruno, you can interact with the cactus runner, for example, by starting a test procedure by sending a *Start* request.

### Running locally with docker

First, the cactus runner docker image needs to be built,

```
cd docker
docker build -t cactus-runner:latest -f Dockerfile --secret id=github_pat,src=./github-pat.txt ../
```

The cactus runner has [envoy](https://github.com/bsgip/envoy) as a dependency and requires a [GitHub Personal Access Token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) stored in `docker/github-pat.txt` to build successfully.

In the `docker` directory, start the docker stack with,

```
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d
```

When using the Bruno API client collection with the dockerised cactus runner, it is necessary to change the collections's `HOST` variable from `http://localhost:8080` to `http:localhost:8000`.



