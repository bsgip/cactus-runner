from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import create_mock_session
from cactus_schema.runner import (
    ClientInteraction,
    CriteriaEntry,
    DataStreamPoint,
    EndDeviceMetadata,
    RunnerStatus,
    StepEventStatus,
    TimelineDataStreamEntry,
)
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import Check
from freezegun import freeze_time

from cactus_runner.app import status
from cactus_runner.app.timeline import Timeline, TimelineDataStream, duration_to_label
from cactus_runner.models import ActiveTestProcedure, CheckResult, StepInfo
from cactus_runner.plugin.backends.common import RunnerBackend
from cactus_runner.plugin.backends.envoy import EnvoyAdminClient
from cactus_runner.plugin.backends.resolver import ExpressionResolver

PENDING_STEP = StepInfo()
RESOLVED_STEP = StepInfo(started_at=datetime.now(tz=UTC), completed_at=datetime.now(tz=UTC))


@pytest.mark.parametrize(
    "step_status,expected",
    [
        ({}, "0/0 steps complete."),
        ({"step_name": PENDING_STEP}, "0/1 steps complete."),
        ({"step_name": RESOLVED_STEP}, "1/1 steps complete."),
        (
            {"step_1": RESOLVED_STEP, "step_2": RESOLVED_STEP, "step_3": PENDING_STEP},
            "2/3 steps complete.",
        ),
    ],
)
def test_get_runner_status_summary(step_status, expected):
    assert status.get_runner_status_summary(step_status=step_status) == expected


BASIS = datetime(2023, 5, 7, tzinfo=UTC)


@pytest.mark.parametrize(
    "resolve_max_w_result, timeline_streams_result, expected_max_w",
    [
        (123.45, [generate_class_instance(TimelineDataStreamEntry)], 123),
        (123.45, Exception, None),
        (Exception, [generate_class_instance(TimelineDataStreamEntry)], None),
        (Exception, Exception, None),
    ],
)
@freeze_time(BASIS)
@pytest.mark.anyio
async def test_get_active_runner_status(mocker, resolve_max_w_result, timeline_streams_result, expected_max_w):
    # Arrange
    mock_run_check = mocker.patch("cactus_runner.app.status.run_check")
    mock_get_timeline_streams = mocker.patch("cactus_runner.app.status.get_timeline_data_streams")
    mock_backend = Mock(spec=RunnerBackend)
    mock_resolver = Mock(spec=ExpressionResolver)
    mock_backend.get_expression_resolver.return_value = mock_resolver
    mock_backend.get_end_device_metadata.return_value = None

    mock_run_check.return_value = CheckResult(True, "Details on Check 1")

    if isinstance(resolve_max_w_result, type):
        mock_resolver.resolve_named_variable_der_setting_max_w.side_effect = resolve_max_w_result()
    else:
        mock_resolver.resolve_named_variable_der_setting_max_w.return_value = resolve_max_w_result

    if isinstance(timeline_streams_result, type):
        mock_get_timeline_streams.side_effect = timeline_streams_result()
    else:
        mock_get_timeline_streams.return_value = timeline_streams_result

    expected_test_name = "TEST_NAME"
    expected_step_status = {
        "step_name": StepEventStatus(started_at=datetime.now(tz=UTC), completed_at=None, event_status=None)
    }
    expected_status_summary = "0/1 steps complete."
    expected_csip_aus_version = CSIPAusVersion.RELEASE_1_2
    expected_started_at = BASIS - timedelta(seconds=123, microseconds=45)
    expected_now_offset = duration_to_label(120)  # This is the interval aligned offset - for a 20s interval

    mock_definition = Mock()
    mock_definition.criteria = Mock()
    criteria_check = Check("check-1", {})
    mock_definition.criteria.checks = [criteria_check]
    mock_definition.preconditions.checks = [criteria_check]  # reuse mocked criteria check

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name=expected_test_name,
        step_status={"step_name": StepInfo(started_at=datetime.now(tz=UTC))},
        csip_aus_version=expected_csip_aus_version,
        definition=mock_definition,
        listeners=[],
        started_at=expected_started_at,
        finished_zip_path=None,
    )

    request_history = Mock()
    last_client_interaction = Mock()

    # Act
    runner_status = await status.get_active_runner_status(
        active_test_procedure=active_test_procedure,
        request_history=request_history,
        last_client_interaction=last_client_interaction,
        backend=mock_backend,
    )

    # Assert
    assert isinstance(runner_status, RunnerStatus)
    assert runner_status.last_client_interaction == last_client_interaction
    assert runner_status.request_history == request_history
    assert runner_status.test_procedure_name == expected_test_name
    assert runner_status.step_status == expected_step_status
    assert runner_status.status_summary == expected_status_summary
    assert isinstance(runner_status.csip_aus_version, str)
    assert runner_status.csip_aus_version == expected_csip_aus_version
    assert runner_status.criteria == [CriteriaEntry(True, "check-1", "Details on Check 1")]
    if expected_max_w is None:
        assert runner_status.timeline is None or runner_status.timeline.set_max_w is None
    else:
        assert runner_status.timeline is not None
        assert runner_status.timeline.set_max_w == expected_max_w
    assert runner_status.end_device_metadata is None

    # If we have timeline data - ensure it's set as expected. Otherwise it should not be there at all
    if not isinstance(timeline_streams_result, type):
        assert runner_status.timeline is not None
        assert runner_status.timeline.now_offset == expected_now_offset
        assert runner_status.timeline.data_streams is timeline_streams_result
    else:
        assert runner_status.timeline is None

    mock_backend.get_expression_resolver.assert_called_once()
    mock_resolver.resolve_named_variable_der_setting_max_w.assert_called_once()
    mock_resolver.resolve_named_variable_der_setting_max_discharge_rate_w.assert_called_once()
    mock_resolver.resolve_named_variable_der_setting_max_charge_rate_w.assert_called_once()


@pytest.mark.anyio
async def test_get_active_runner_status_calls_get_runner_status_summary(mocker):
    get_runner_status_summary_spy = mocker.spy(status, "get_runner_status_summary")

    mock_backend = Mock(spec=RunnerBackend)
    active_test_procedure = Mock()
    active_test_procedure.step_status = {"step_name": StepInfo()}
    active_test_procedure.listeners = []
    active_test_procedure.definition = Mock()
    active_test_procedure.definition.criteria = None
    active_test_procedure.definition.preconditions.checks = None
    active_test_procedure.listeners = []
    request_history = Mock()
    last_client_interaction = Mock()
    mock_envoy_client = Mock(spec=EnvoyAdminClient)

    _ = await status.get_active_runner_status(
        active_test_procedure=active_test_procedure,
        request_history=request_history,
        last_client_interaction=last_client_interaction,
        backend=mock_backend,
    )
    get_runner_status_summary_spy.assert_called_once_with(step_status=active_test_procedure.step_status)
    mock_backend.get_expression_resolver.assert_called_once()
    mock_envoy_client.assert_not_called()


@pytest.mark.anyio
async def test_get_active_runner_status_with_end_device_metadata(mocker):
    """Test that EndDeviceMetadata is correctly populated from active site"""
    # Arrange
    mocker.patch("cactus_runner.app.status.run_check", return_value=CheckResult(True, "Check passed"))
    mocker.patch("cactus_runner.app.status.get_timeline_data_streams", return_value=[])
    mock_backend = Mock(spec=RunnerBackend)
    mock_resolver = Mock(spec=ExpressionResolver)
    mock_backend.get_expression_resolver.return_value = mock_resolver
    mock_resolver.resolve_named_variable_der_setting_max_w.return_value = 5000

    meta_metadata = generate_class_instance(EndDeviceMetadata, seed=101, aggregator_id=1, edevid=42)

    mock_backend.get_end_device_metadata.return_value = meta_metadata

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name="TEST_NAME",
        step_status={},
        csip_aus_version=CSIPAusVersion.RELEASE_1_2,
        definition=Mock(criteria=Mock(checks=[]), preconditions=Mock(checks=[])),
        listeners=[],
        started_at=None,
        finished_zip_path=None,
    )

    # Act
    runner_status = await status.get_active_runner_status(active_test_procedure, Mock(), Mock(), mock_backend)

    # Assert - EndDeviceMetadata
    metadata: EndDeviceMetadata | None = runner_status.end_device_metadata
    assert metadata == meta_metadata

    mock_backend.get_expression_resolver.assert_called_once()
    assert len(mock_resolver.mock_calls) >= 1
    mock_resolver.resolve_named_variable_der_setting_max_w.assert_called_once()
    mock_resolver.resolve_named_variable_der_setting_max_charge_rate_w.assert_called_once()
    mock_resolver.resolve_named_variable_der_setting_max_discharge_rate_w.assert_called_once()


@pytest.mark.anyio
async def test_get_active_runner_status_end_device_metadata_handles_errors(mocker):
    """Test that EndDeviceMetadata is None when backend.get_active_runner_status raises an exception"""
    mocker.patch("cactus_runner.app.status.run_check", return_value=CheckResult(True, "Check passed"))
    mocker.patch("cactus_runner.app.status.get_timeline_data_streams", return_value=[])
    mock_backend = Mock(spec=RunnerBackend)
    mock_resolver = Mock(spec=ExpressionResolver)
    mock_backend.get_expression_resolver.return_value = mock_resolver
    mock_resolver.resolve_named_variable_der_setting_max_w.return_value = 5000
    mock_backend.get_end_device_metadata.side_effect = Exception("DB error")

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name="TEST_NAME",
        step_status={},
        csip_aus_version=CSIPAusVersion.RELEASE_1_2,
        definition=Mock(criteria=Mock(checks=[]), preconditions=Mock(checks=[])),
        listeners=[],
        started_at=None,
        finished_zip_path=None,
    )

    runner_status = await status.get_active_runner_status(active_test_procedure, Mock(), Mock(), mock_backend)

    assert runner_status.end_device_metadata is None

    mock_backend.get_expression_resolver.assert_called_once()
    assert len(mock_resolver.mock_calls) >= 1
    mock_resolver.resolve_named_variable_der_setting_max_w.assert_called_once()
    mock_resolver.resolve_named_variable_der_setting_max_charge_rate_w.assert_called_once()
    mock_resolver.resolve_named_variable_der_setting_max_discharge_rate_w.assert_called_once()


def test_get_runner_status(example_client_interaction: ClientInteraction):
    runner_status = status.get_runner_status(last_client_interaction=example_client_interaction)

    assert isinstance(runner_status, RunnerStatus)
    assert runner_status.status_summary == "No test procedure running"
    assert runner_status.last_client_interaction == example_client_interaction
    assert runner_status.test_procedure_name == "-"
    assert runner_status.csip_aus_version == ""
    assert runner_status.step_status is None
    assert runner_status.request_history == []


@pytest.mark.parametrize(
    "interval_seconds, data_streams, expected_data_streams",
    [
        (123, [], []),
        (
            13,
            [TimelineDataStream("label 1", [None, 123, -456, None], stepped=True, dashed=False)],
            [
                TimelineDataStreamEntry(
                    "label 1",
                    [
                        DataStreamPoint(None, duration_to_label(0)),
                        DataStreamPoint(123, duration_to_label(13)),
                        DataStreamPoint(-456, duration_to_label(26)),
                        DataStreamPoint(None, duration_to_label(39)),
                    ],
                    stepped=True,
                    dashed=False,
                )
            ],
        ),
        (
            7,
            [
                TimelineDataStream("label 11", [0, 1], stepped=False, dashed=True),
                TimelineDataStream("label 22", [0, 0], stepped=True, dashed=False),
            ],
            [
                TimelineDataStreamEntry(
                    "label 11",
                    [
                        DataStreamPoint(0, duration_to_label(0)),
                        DataStreamPoint(1, duration_to_label(7)),
                    ],
                    stepped=False,
                    dashed=True,
                ),
                TimelineDataStreamEntry(
                    "label 22",
                    [
                        DataStreamPoint(0, duration_to_label(0)),
                        DataStreamPoint(0, duration_to_label(7)),
                    ],
                    stepped=True,
                    dashed=False,
                ),
            ],
        ),
    ],
)
@pytest.mark.anyio
async def test_get_timeline_data_streams(mocker, interval_seconds, data_streams, expected_data_streams):
    """Tests whether converting to the status timeline model raises any issues"""
    mock_session = create_mock_session()
    start = datetime(2024, 11, 5, tzinfo=UTC)
    end = datetime(2024, 11, 6, tzinfo=UTC)

    mock_generate_timeline = mocker.patch("cactus_runner.app.status.generate_timeline")
    mock_generate_timeline.return_value = generate_class_instance(Timeline, data_streams=data_streams)

    result = await status.get_timeline_data_streams(mock_session, start, interval_seconds, end)
    assert_list_type(TimelineDataStreamEntry, result, len(expected_data_streams))
    assert result == expected_data_streams
    mock_generate_timeline.assert_called_once_with(mock_session, start, interval_seconds, end)


@freeze_time(BASIS)
@pytest.mark.anyio
async def test_get_active_runner_status_with_cropping(mocker):
    # Arrange
    now = BASIS
    mocker.patch("cactus_runner.app.status.run_check", return_value=CheckResult(True, "Check passed"))
    mock_backend = Mock(spec=RunnerBackend)
    mock_resolver = Mock(spec=ExpressionResolver)
    mock_backend.get_expression_resolver.return_value = mock_resolver
    mock_resolver.resolve_named_variable_der_setting_max_w.return_value = 5000

    mock_get_timeline_streams = mocker.patch("cactus_runner.app.status.get_timeline_data_streams")
    mock_timeline_data = [generate_class_instance(TimelineDataStreamEntry)]
    mock_get_timeline_streams.return_value = mock_timeline_data

    test_started_at = now - timedelta(minutes=60)

    # request history spanning 60 minutes
    request_history = [
        Mock(timestamp=now - timedelta(minutes=60)),
        Mock(timestamp=now - timedelta(minutes=50)),
        Mock(timestamp=now - timedelta(minutes=40)),
        Mock(timestamp=now - timedelta(minutes=30)),
        Mock(timestamp=now - timedelta(minutes=20)),
        Mock(timestamp=now - timedelta(minutes=14)),  # keep below here only
        Mock(timestamp=now - timedelta(minutes=10)),
        Mock(timestamp=now - timedelta(minutes=5)),
        Mock(timestamp=now - timedelta(minutes=1)),
    ]

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name="TEST_CROP",
        step_status={},
        csip_aus_version=CSIPAusVersion.RELEASE_1_2,
        definition=Mock(criteria=Mock(checks=[]), preconditions=Mock(checks=[])),
        listeners=[],
        started_at=test_started_at,
        finished_zip_path=None,
    )

    last_client_interaction = Mock()

    # Act - crop to last 15 minutes
    runner_status = await status.get_active_runner_status(
        active_test_procedure=active_test_procedure,
        request_history=request_history,
        last_client_interaction=last_client_interaction,
        crop_minutes=15,
        backend=mock_backend,
    )

    # Assert - request_history should only contain last 15 minutes
    assert len(runner_status.request_history) == 4
    assert runner_status.request_history[0].timestamp == now - timedelta(minutes=14)
    assert runner_status.request_history[1].timestamp == now - timedelta(minutes=10)
    assert runner_status.request_history[2].timestamp == now - timedelta(minutes=5)
    assert runner_status.request_history[3].timestamp == now - timedelta(minutes=1)

    # Assert - timeline basis should be adjusted to 15 minutes ago (not 60 minutes ago)
    expected_crop_start = now - timedelta(minutes=15)
    expected_basis = max(test_started_at, expected_crop_start)  # Should be expected_crop_start
    expected_end = now + timedelta(seconds=120)

    mock_get_timeline_streams.assert_called_once_with(
        mock_backend,
        expected_basis,  # Should be cropped basis, not original test_started_at
        20,  # interval_seconds
        expected_end,
    )

    assert runner_status.timeline is not None
    assert runner_status.timeline.data_streams == mock_timeline_data

    mock_backend.get_expression_resolver.assert_called_once()
    mock_resolver.resolve_named_variable_der_setting_max_w.assert_called_once()
    mock_resolver.resolve_named_variable_der_setting_max_discharge_rate_w.assert_called_once()
    mock_resolver.resolve_named_variable_der_setting_max_charge_rate_w.assert_called_once()
