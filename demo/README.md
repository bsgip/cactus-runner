# cactus-teststack Demo

This directory provides a self-contained demo environment for evaluating and running CSIP-AUS test cases, as defined in [cactus-test-definitions](https://github.com/bsgip/cactus-test-definitions), using the cactus **teststack** for client testing.

This is useful for:
- Evaluating the coverage of the compliance test cases.
- Running and inspecting the behavior of specific test cases.
- Validating updates to the **teststack** components in isolation.
- Enabling local testing to support CSIP-AUS client development.


## Demo Overview
The **teststack** is orchestrated using Docker Compose in this demo. This `demo/` directory contains a `docker-compose.yml` that:
- Starts the test runner and reference CSIP-AUS server stack.
- Initializes the database with appropriate schema/data.
- Launches all required services for running test cases end-to-end.

The **teststack** is a complete suite of components for testing a client implementation against the CSIP-AUS standard. It includes:
- The `cactus-runner`: a test runner responsible for executing and managing test case logic.
- The [`envoy`](https://github.com/bsgip/envoy) CSIP-AUS reference implementation server and its associated components.

## Requirements

To run the demo stack, you will need:
- `docker` v28.0.4 or later
- `docker-compose` v2.32.0 or later
- [`bruno`](https://www.usebruno.com/) v1.40.1 or later — for executing the test request collections

## Usage

### (1) Launch the teststack

Navigate to the `demo/` directory and bring up the teststack using Docker Compose:

`docker compose up`

**NOTE:** This will partially bring up the teststack components, specifically `cactus-runner`, `cactus-envoy-db` and `cactus-teststack-init`. The remaining components will be launched during after a test case is initialised.

### (2) Open Bruno and Load the Request Collection
Launch Bruno then open the example collection provided in the repository under the `bruno` directory. This collection contains the predefined HTTP requests needed to both facilitate and run test cases against the teststack. This should require no configuration.

### (3) Running a Test Case
Follow this typical flow for starting and then running against test case:

1. **Init the Case**

    - Use the Init request in Bruno to register and initialise a test case. This is the *precondition* phase of the test where the cactus-runner expects the client to populate the utility server with the required metadata as defined in the test cases themselves. 

    - **NOTE:** This step will trigger the remaining teststack components (`cactus-envoy` + notification components) to be spun up, which may take a few seconds. Please wait until output logs halt before proceeding to the next step.

2. **Start the Case**

    - Use the Start request to trigger test execution. This begins the simulated scenario and enables the server to receive CSIP-AUS requests.

3. **Execute the Case**

    - Use the relevant sub-collections e.g. ALL-01 which contains requests for the selected test procedure. 

4. **Finalize the Case**

    - Once all interactions are complete, send the Finalize request to end the test case, allowing the runner to wrap up the scenario and output results.