from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest
from assertical.fake.generator import generate_class_instance
from cactus_test_definitions import TestProcedureConfig
from envoy.server.model import DynamicOperatingEnvelope, Site, SiteReadingType
from envoy_schema.server.schema.sep2.types import DeviceCategory

from cactus_runner.app.check import CheckResult
from cactus_runner.app.reporting import device_category_to_string, pdf_report_as_bytes
from cactus_runner.models import (
    ActiveTestProcedure,
    RequestEntry,
    RunnerState,
    StepStatus,
)


def test_pdf_report_as_bytes():
    # Arrange
    definitions = TestProcedureConfig.from_resource()
    test_name = "ALL-01"
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name=test_name,
        definition=definitions.test_procedures[test_name],
        step_status={"1": StepStatus.PENDING},
        finished_zip_data=None,
        run_id=None,
    )
    NUM_REQUESTS = 3
    runner_state = RunnerState(
        active_test_procedure=active_test_procedure,
        request_history=[generate_class_instance(RequestEntry) for _ in range(NUM_REQUESTS)],
    )

    NUM_CHECK_RESULTS = 3
    check_results = {f"check{i}": generate_class_instance(CheckResult) for i in range(NUM_CHECK_RESULTS)}

    NUM_READING_TYPES = 3
    sample_readings = pd.DataFrame(
        {
            "scaled_value": [Decimal(1.0)],
            "time_period_start": [datetime.now(timezone.utc)],
        }
    )
    readings = {generate_class_instance(SiteReadingType): sample_readings for _ in range(NUM_READING_TYPES)}

    reading_counts = {generate_class_instance(SiteReadingType): i for i in range(NUM_READING_TYPES)}

    NUM_SITES = 2
    sites = [generate_class_instance(Site) for _ in range(NUM_SITES)]

    NUM_CONTROLS = 3
    controls = [generate_class_instance(DynamicOperatingEnvelope) for _ in range(NUM_CONTROLS)]

    # Act
    report_bytes = pdf_report_as_bytes(
        runner_state=runner_state,
        check_results=check_results,
        readings=readings,
        reading_counts=reading_counts,
        sites=sites,
        controls=controls,
    )

    # Assert - we are mainly checking that no uncaught exceptions are raised generating the pdf report
    assert len(report_bytes) > 0


def test_pdf_report_as_bytes_does_raise_exception_for_large_amount_of_validation_errors():
    # Arrange
    definitions = TestProcedureConfig.from_resource()
    test_name = "ALL-01"
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        name=test_name,
        definition=definitions.test_procedures[test_name],
        step_status={"1": StepStatus.PENDING},
        finished_zip_data=None,
        run_id=None,
    )

    # Check a request with many validation errors doesn't break the pdf generation
    body_xml_errors = [
        (
            "lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum\n"
            "lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum\n"
            "lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum lorem ipsum\n"
        )
    ] * 20
    runner_state = RunnerState(
        active_test_procedure=active_test_procedure,
        request_history=[generate_class_instance(RequestEntry, body_xml_errors=body_xml_errors)],
    )
    NUM_CHECK_RESULTS = 3
    check_results = {f"check{i}": generate_class_instance(CheckResult) for i in range(NUM_CHECK_RESULTS)}

    # Act
    pdf_report_as_bytes(
        runner_state=runner_state,
        check_results=check_results,
        readings={},
        reading_counts={},
        sites=[],
        controls=[],
    )

    # There should be not exceptions raises do to Flowables being too large


@pytest.mark.parametrize(
    "device_category,expected",
    [
        (DeviceCategory(0), "Unspecified device category (0)"),
        (DeviceCategory.SMART_ENERGY_MODULE, "smart energy module"),
        (DeviceCategory.SMART_ENERGY_MODULE | DeviceCategory.WATER_HEATER, "water heater | smart energy module"),
    ],
)
def test_device_category_to_string(device_category: DeviceCategory, expected: str):
    assert device_category_to_string(device_category=device_category) == expected
