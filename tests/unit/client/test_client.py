from datetime import datetime, timezone
from itertools import product
from unittest.mock import MagicMock

import pytest
from aiohttp import ConnectionTimeoutError
from cactus_test_definitions import CSIPAusVersion, TestProcedureId

from cactus_runner.client import RunnerClient, RunnerClientException
from cactus_runner.models import (
    ClientInteraction,
    InitResponseBody,
    RunnerStatus,
    StartResponseBody,
)


@pytest.mark.parametrize("subscription_domain, run_id", product([None, "my.fq.dn"], [None, "abc 123"]))
@pytest.mark.asyncio
async def test_init_varying_domain_run_id(subscription_domain: str | None, run_id: str | None):
    # Arrange
    expected_start_result = InitResponseBody(
        status="PLACEHOLDER-STATUS", test_procedure="ALL-01", timestamp=datetime.now(timezone.utc)
    )
    test_id = TestProcedureId.ALL_01
    csip_aus_version = CSIPAusVersion.RELEASE_1_2
    aggregator_certificate = """asdf"""
    device_certificate = """zxcv"""
    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value.status = 200
    mock_session.post.return_value.__aenter__.return_value.text.return_value = expected_start_result.to_json()

    # Act
    start_result = await RunnerClient.init(
        session=mock_session,
        test_id=test_id,
        csip_aus_version=csip_aus_version,
        aggregator_certificate=aggregator_certificate,
        device_certificate=device_certificate,
        subscription_domain=subscription_domain,
    )

    # Assert
    if subscription_domain is None and run_id is None:
        mock_session.post_assert_called_once_with(
            url="/init",
            params={
                "test": test_id.value,
                "aggregator_certificate": aggregator_certificate,
                "device_certificate": device_certificate,
            },
        )
    elif subscription_domain is None:
        mock_session.post_assert_called_once_with(
            url="/init",
            params={
                "test": test_id.value,
                "aggregator_certificate": aggregator_certificate,
                "device_certificate": device_certificate,
                "run_id": run_id,
            },
        )
    elif run_id is None:
        mock_session.post_assert_called_once_with(
            url="/init",
            params={
                "test": test_id.value,
                "aggregator_certificate": aggregator_certificate,
                "device_certificate": device_certificate,
                "subscription_domain": subscription_domain,
            },
        )
    else:
        mock_session.post_assert_called_once_with(
            url="/init",
            params={
                "test": test_id.value,
                "aggregator_certificate": aggregator_certificate,
                "device_certificate": device_certificate,
                "subscription_domain": subscription_domain,
                "run_id": run_id,
            },
        )
    assert mock_session.post.return_value.__aenter__.return_value.text.call_count == 1
    assert isinstance(start_result, InitResponseBody)
    assert start_result == expected_start_result


@pytest.mark.parametrize("aggregator_certificate, device_certificate", product([None, "cert123"], [None, "cert456"]))
@pytest.mark.asyncio
async def test_init_varying_certificates(aggregator_certificate: str | None, device_certificate: str | None):
    # Arrange
    expected_start_result = InitResponseBody(
        status="PLACEHOLDER-STATUS", test_procedure="ALL-01", timestamp=datetime.now(timezone.utc)
    )
    test_id = TestProcedureId.ALL_01
    csip_aus_version = CSIPAusVersion.RELEASE_1_2
    subscription_domain = "fq.dn"
    run_id = "ALL-01"
    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value.status = 200
    mock_session.post.return_value.__aenter__.return_value.text.return_value = expected_start_result.to_json()

    # Act
    start_result = await RunnerClient.init(
        session=mock_session,
        test_id=test_id,
        csip_aus_version=csip_aus_version,
        aggregator_certificate=aggregator_certificate,
        device_certificate=device_certificate,
        subscription_domain=subscription_domain,
    )

    # Assert
    if aggregator_certificate is None and device_certificate is None:
        mock_session.post_assert_called_once_with(
            url="/init",
            params={
                "test": test_id.value,
                "subscription_domain": subscription_domain,
                "run_id": run_id,
            },
        )
    elif aggregator_certificate is None:
        mock_session.post_assert_called_once_with(
            url="/init",
            params={
                "test": test_id.value,
                "device_certificate": device_certificate,
                "subscription_domain": subscription_domain,
                "run_id": run_id,
            },
        )
    elif device_certificate is None:
        mock_session.post_assert_called_once_with(
            url="/init",
            params={
                "test": test_id.value,
                "aggregator_certificate": aggregator_certificate,
                "subscription_domain": subscription_domain,
                "run_id": run_id,
            },
        )
    else:
        mock_session.post_assert_called_once_with(
            url="/init",
            params={
                "test": test_id.value,
                "aggregator_certificate": aggregator_certificate,
                "device_certificate": device_certificate,
                "subscription_domain": subscription_domain,
                "run_id": run_id,
            },
        )
    assert mock_session.post.return_value.__aenter__.return_value.text.call_count == 1
    assert isinstance(start_result, InitResponseBody)
    assert start_result == expected_start_result


@pytest.mark.asyncio
async def test_init_connectionerror():
    # Arrange
    mock_session = MagicMock()
    mock_session.post.side_effect = ConnectionTimeoutError

    # Act/Assert
    with pytest.raises(RunnerClientException, match="Unexpected failure while initialising test."):
        _ = await RunnerClient.init(
            session=mock_session,
            test_id=TestProcedureId.ALL_01,
            csip_aus_version=CSIPAusVersion.RELEASE_1_2,
            aggregator_certificate="FAKE_AGGREGATOR_CERT",
            device_certificate=None,
        )


@pytest.mark.asyncio
async def test_start():
    # Arrange
    expected_start_result = StartResponseBody(
        status="PLACEHOLDER-STATUS", test_procedure="ALL-01", timestamp=datetime.now(timezone.utc)
    )
    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value.status = 200
    mock_session.post.return_value.__aenter__.return_value.text.return_value = expected_start_result.to_json()

    # Act
    start_result = await RunnerClient.start(session=mock_session)

    # Assert
    mock_session.post_assert_called_once_with(url="/start")
    assert mock_session.post.return_value.__aenter__.return_value.text.call_count == 1
    assert isinstance(start_result, StartResponseBody)
    assert start_result == expected_start_result


@pytest.mark.asyncio
async def test_start_connectionerror():
    # Arrange
    mock_session = MagicMock()
    mock_session.post.side_effect = ConnectionTimeoutError

    # Act/Assert
    with pytest.raises(RunnerClientException, match="Unexpected failure while starting test."):
        _ = await RunnerClient.start(session=mock_session)


@pytest.mark.asyncio
async def test_finalize():
    # Arrange
    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value.status = 200
    mock_session.post.return_value.__aenter__.return_value.read.return_value = bytes()

    # Act
    finalize_result = await RunnerClient.finalize(session=mock_session)

    # Assert
    mock_session.post.assert_called_once_with(url="/finalize")
    assert mock_session.post.return_value.__aenter__.return_value.read.call_count == 1
    assert isinstance(finalize_result, bytes)


@pytest.mark.asyncio
async def test_finalize_connectionerror():
    # Arrange
    mock_session = MagicMock()
    mock_session.post.side_effect = ConnectionTimeoutError

    # Act/Assert
    with pytest.raises(RunnerClientException, match="Unexpected failure while finalizing test procedure."):
        _ = await RunnerClient.finalize(session=mock_session)


@pytest.mark.asyncio
async def test_status(runner_status_fixture):
    # Arrange
    expected_status = runner_status_fixture
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value.status = 200
    mock_session.get.return_value.__aenter__.return_value.text.return_value = expected_status.to_json()

    # Act
    status = await RunnerClient.status(session=mock_session)

    # Assert
    mock_session.get.assert_called_once_with(url="/status")
    assert mock_session.get.return_value.__aenter__.return_value.text.call_count == 1
    assert isinstance(status, RunnerStatus)
    assert status == expected_status


@pytest.mark.asyncio
async def test_status_connectionerror():
    # Arrange
    mock_session = MagicMock()
    mock_session.get.side_effect = ConnectionTimeoutError

    # Act/Assert
    with pytest.raises(RunnerClientException, match="Unexpected failure while requesting test procedure status."):
        _ = await RunnerClient.status(session=mock_session)


@pytest.mark.asyncio
async def test_last_interaction(runner_status_fixture):
    # Arrange
    expected_last_interaction = runner_status_fixture.last_client_interaction
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value.status = 200
    mock_session.get.return_value.__aenter__.return_value.text.return_value = runner_status_fixture.to_json()

    # Act
    last_interaction = await RunnerClient.last_interaction(session=mock_session)

    # Assert
    mock_session.get.assert_called_once_with(url="/status")
    assert mock_session.get.return_value.__aenter__.return_value.text.call_count == 1
    assert isinstance(last_interaction, ClientInteraction)
    assert last_interaction == expected_last_interaction


@pytest.mark.asyncio
async def test_last_interaction_connectionerror():
    # Arrange
    mock_session = MagicMock()
    mock_session.get.side_effect = ConnectionTimeoutError

    # Act/Assert
    with pytest.raises(RunnerClientException, match="Unexpected failure while requesting test procedure status."):
        _ = await RunnerClient.last_interaction(session=mock_session)
