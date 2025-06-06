# Cactus Runner

Cactus Runner is a component of the Client CSIP-AUS Test Harness.

## Demo (Example)

Cactus Runner has a full demo for evaluation purposes. See the [demo/](demo/README.md) directory for instructions on how to get the demo up and running.

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

The `cactus_runner` package (provided by this repo) should be installed into a suitable virtual environment. Activate your virtual environment and then run,

```sh
pip install --editable .[dev,test]
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

Next we need to define the [environment variables](#environment-variables). The following is a sample .env that should get you started.

```sh
SERVER_URL=http://cactus-envoy:8000
DATABASE_URL=postgresql+psycopg://test_user:test_pwd@cactus-envoy-db/test_db
ENVOY_ADMIN_BASICAUTH_USERNAME=admin
ENVOY_ADMIN_BASICAUTH_PASSWORD=password
ENVOY_ADMIN_URL=http://cactus-envoy-admin:8001
```

### Running Cactus Runner

The easiest and preferred way is to bring up the [demo](/demo/README.md). This uses pre-built docker images for all services including the cactus runner.

For local development however it may be easier to run the cactus runner locally.

1.Start the docker compose stack (in the project root),

```
docker compose up
```

This brings up all the services that are required to run, for example, the utility server (envoy) and it's database (envoy-db). Like for the demo, this uses prebuilt images pulled from our publically accessible Azure registry.

2. Start the cactus-runner,

```
dotenv run -- python src/cactus_runner/app/main.py
```

The `dotenv` command makes the environment variables in a .env file available to the cactus runner. See the [Setup](#setup) section for what to include in the .env file. 

3. Use Bruno to interact with the cactus runner. See ["Running a Test Case"](demo/README.md) section from the demo README on how to do this.

