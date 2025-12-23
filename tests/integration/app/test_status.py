from datetime import datetime

import pytest
from aiohttp import ClientSession, ClientTimeout
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from cactus_schema.runner import RunnerStatus, RunRequest
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedureId
from envoy.server.model.site import Site, SiteDER, SiteDERSetting
from pytest_aiohttp.plugin import TestClient

from cactus_runner.client import RunnerClient
from tests.integration.certificate1 import (
    TEST_CERTIFICATE_PEM as TEST_CERTIFICATE_1_PEM,
)


@pytest.mark.slow
@pytest.mark.anyio
async def test_status_end_device_metadata(cactus_runner_client: TestClient, pg_base_config, run_request_generator):
    """Tests that end_device_metadata is correctly populated with eager loading of site_ders"""

    aggregator_cert = TEST_CERTIFICATE_1_PEM.decode()

    run_request: RunRequest = run_request_generator(
        TestProcedureId.ALL_01, aggregator_cert, None, CSIPAusVersion.RELEASE_1_2, None
    )

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        init_response = await RunnerClient.initialise(session, run_request)
        assert init_response.is_started

    # Create a NEW site with all the values we want to test
    async with generate_async_session(pg_base_config) as db_session:
        new_site = generate_class_instance(
            Site,
            aggregator_id=1,
            site_id=None,
            lfdi="aabbccddeeff00112233445566778899aabbccdd",
            sfdi=1234567890,
            nmi="1234567890A",
            device_category=1,
            timezone_id="Australia/Sydney",
            changed_time=datetime.now(),
        )
        db_session.add(new_site)

        new_der = generate_class_instance(SiteDER, site=new_site)
        db_session.add(new_der)

        new_der_settings = generate_class_instance(
            SiteDERSetting, site_der=new_der, max_w_value=5000, max_w_multiplier=0, doe_modes_enabled=7
        )
        db_session.add(new_der_settings)

        await db_session.commit()

    # Fetch status and verify end_device_metadata is populated from our new site
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        status_response = await RunnerClient.status(session)

    assert isinstance(status_response, RunnerStatus)
    assert status_response.end_device_metadata is not None, "end_device_metadata should not be None"

    # Verify all values from our newly created site (the most recently changed)
    metadata = status_response.end_device_metadata
    assert metadata.lfdi == "aabbccddeeff00112233445566778899aabbccdd"
    assert metadata.sfdi == 1234567890
    assert metadata.nmi == "1234567890A"
    assert metadata.aggregator_id == 1
    assert metadata.set_max_w == 5000
    assert metadata.doe_modes_enabled == 7
    assert metadata.device_category == 1
    assert metadata.timezone_id == "Australia/Sydney"
