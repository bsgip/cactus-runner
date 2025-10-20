from datetime import datetime, timedelta, timezone
from decimal import Decimal
import http
import random
import pandas as pd
import pytest
from assertical.fake.generator import generate_class_instance
from cactus_test_definitions.client import TestProcedureConfig
from envoy.server.model import (
    Site,
    SiteDER,
    SiteDERSetting,
    SiteReadingType,
    SiteDERRating,
    SiteDERAvailability,
    SiteDERStatus,
    KindType,
    UomType,
    RoleFlagsType,
    DataQualifierType,
)
from envoy_schema.server.schema.sep2.types import DeviceCategory
from cactus_runner.app.check import CheckResult
from cactus_runner.app.reporting import device_category_to_string, pdf_report_as_bytes
from cactus_runner.app.timeline import Timeline, TimelineDataStream
from cactus_runner.models import (
    ActiveTestProcedure,
    ClientInteraction,
    ClientInteractionType,
    RequestEntry,
    RunnerState,
    StepInfo,
)
from cactus_runner.app.envoy_common import ReadingLocation

DT_NOW = datetime.now(timezone.utc)


def active_test_procedure(test_name="ALL-01", step_status=None, witness_testing=False, run_id=None, **kwargs):
    definitions = TestProcedureConfig.from_resource()
    definition = definitions.test_procedures[test_name]
    if witness_testing:
        definition.classes = ["DER-A", "DER-B"]

    return generate_class_instance(
        ActiveTestProcedure,
        name=test_name,
        definition=definition,
        step_status=step_status or {"1": StepInfo()},
        finished_zip_data=None,
        run_id=run_id,
        **kwargs,
    )


def runner_state(request_history=None, client_interactions=None, active_test=None, num_requests=3, **test_kwargs):
    if active_test is None:
        active_test = active_test_procedure(**test_kwargs)
    if request_history is None:
        request_history = [generate_class_instance(RequestEntry) for _ in range(num_requests)]

    return RunnerState(
        active_test_procedure=active_test,
        request_history=request_history,
        client_interactions=client_interactions or [],
    )


def check_results(num=3, passed=True, description=None):
    return {
        f"check{i}": generate_class_instance(CheckResult, passed=passed, description=description) for i in range(num)
    }


def readings(num=3):
    sample_df = pd.DataFrame(
        {
            "scaled_value": [Decimal(1.0)],
            "time_period_start": [DT_NOW],
        }
    )
    return {generate_class_instance(SiteReadingType): sample_df for _ in range(num)}


def reading_counts(num=3):
    return {generate_class_instance(SiteReadingType): i for i in range(num)}


def sites(num=2, with_ders=True, optional_is_none=False):
    site_list = []
    for _ in range(num):
        site_ders = []
        if with_ders:
            site_ders = [
                generate_class_instance(
                    SiteDER,
                    site_der_setting=generate_class_instance(
                        SiteDERSetting, max_w_value=6, max_w_multiplier=3, optional_is_none=optional_is_none
                    ),
                    site_der_rating=generate_class_instance(SiteDERRating, optional_is_none=optional_is_none),
                    site_der_availability=generate_class_instance(
                        SiteDERAvailability, optional_is_none=optional_is_none
                    ),
                    site_der_status=generate_class_instance(SiteDERStatus, optional_is_none=optional_is_none),
                )
            ]
        site_list.append(generate_class_instance(Site, site_ders=site_ders))
    return site_list


def timeline():
    return Timeline(
        DT_NOW - timedelta(seconds=350),
        20,
        [
            TimelineDataStream(
                "/derp/1 opModExpLimW", [None, None, None, -3000, -3000, -3000, -2000, -2000, -1500, -1000], True, False
            ),
            TimelineDataStream(
                "/derp/1 opModImpLimW", [5000, 5000, 5000, None, None, 5000, 5000, None, 4500, 4000], True, False
            ),
            TimelineDataStream(
                "Site Watts", [1000, 1150, 800, 600, 500, -500, -1000, -2000, -1500, -1200], False, False
            ),
            TimelineDataStream("Device Watts", [None, None, -1000, -3000, -2250, -100, 0, 0, 500, 1000], False, False),
            TimelineDataStream(
                "Default opModImpLimW", [None, None, 1000, 1000, 1500, 1500, 0, 0, 2000, 2500], True, True
            ),
            TimelineDataStream(
                "Default opModExpLimW", [-5000, -5000, 0, 0, -1000, -1000, -1000, -2000, -2500, -3000], True, True
            ),
        ],
    )


def client_interactions():
    return [
        generate_class_instance(
            ClientInteraction, interaction_type=ClientInteractionType.TEST_PROCEDURE_INIT, timestamp=DT_NOW
        ),
        generate_class_instance(
            ClientInteraction,
            interaction_type=ClientInteractionType.TEST_PROCEDURE_START,
            timestamp=DT_NOW + timedelta(seconds=5),
        ),
    ]


def request_history_comprehensive():
    """Need lots to test request chart"""
    templates = [
        ("/dcap", http.HTTPMethod.GET, http.HTTPStatus.OK, 320, "Init"),
        ("/edev", http.HTTPMethod.GET, http.HTTPStatus.OK, 310, "Unmatched"),
        ("/tm", http.HTTPMethod.GET, http.HTTPStatus.OK, 300, "Unmatched"),
        ("/dcap", http.HTTPMethod.GET, http.HTTPStatus.OK, 280, "GET-DCAP"),
        ("/dcap", http.HTTPMethod.GET, http.HTTPStatus.OK, 278, "GET-DCAP"),
        ("/edev", http.HTTPMethod.GET, http.HTTPStatus.OK, 260, "GET-EDEV-LIST"),
        ("/edev", http.HTTPMethod.GET, http.HTTPStatus.OK, 258, "GET-EDEV-LIST"),
        ("/edev", http.HTTPMethod.GET, http.HTTPStatus.OK, 256, "GET-EDEV-LIST"),
        ("/tm", http.HTTPMethod.GET, http.HTTPStatus.OK, 240, "GET-TM"),
        ("/tm", http.HTTPMethod.GET, http.HTTPStatus.OK, 238, "GET-TM"),
        ("/edev/1/der", http.HTTPMethod.GET, http.HTTPStatus.OK, 220, "GET-DER"),
        ("/edev/1/der/derg", http.HTTPMethod.GET, http.HTTPStatus.OK, 200, "GET-DER-SETTINGS"),
        ("/edev/1/der/derg", http.HTTPMethod.GET, http.HTTPStatus.OK, 198, "GET-DER-SETTINGS"),
        ("/edev/1/der/ders", http.HTTPMethod.GET, http.HTTPStatus.OK, 180, "GET-DER-STATUS"),
        ("/edev/1/der/ders", http.HTTPMethod.GET, http.HTTPStatus.OK, 178, "GET-DER-STATUS"),
        ("/edev/1/der/ders", http.HTTPMethod.GET, http.HTTPStatus.OK, 176, "GET-DER-STATUS"),
        ("/edev/1/derp", http.HTTPMethod.PUT, http.HTTPStatus.CREATED, 160, "PUT-DERP"),
        ("/edev/1/derp/1", http.HTTPMethod.GET, http.HTTPStatus.OK, 158, "PUT-DERP"),
        ("/edev", http.HTTPMethod.GET, http.HTTPStatus.OK, 140, "Unmatched"),
        ("/tm", http.HTTPMethod.GET, http.HTTPStatus.OK, 120, "Unmatched"),
    ]

    return [
        generate_class_instance(
            RequestEntry,
            url=f"https://example.com{path}",
            path=path,
            method=method,
            status=status,
            timestamp=DT_NOW - timedelta(seconds=sec),
            step_name=step,
            body_xml_errors=[],
        )
        for path, method, status, sec, step in templates
    ]


@pytest.mark.parametrize("no_spacers", [True, False])
def test_pdf_report_as_bytes(no_spacers):
    state = runner_state()
    report = pdf_report_as_bytes(
        runner_state=state,
        check_results=check_results(),
        readings=readings(),
        reading_counts=reading_counts(),
        sites=sites(),
        timeline=timeline(),
        no_spacers=no_spacers,
    )
    # Assert - we are mainly checking that no uncaught exceptions are raised generating the pdf report
    assert len(report) > 0


def test_pdf_report_as_bytes_doesnt_raise_exception_for_large_amount_of_validation_errors():
    xml_errors = [("lorem ipsum " * 10 + "\n") * 3] * 20
    state = runner_state(
        request_history=[generate_class_instance(RequestEntry, body_xml_errors=xml_errors)], num_requests=1
    )

    pdf_report_as_bytes(
        runner_state=state, check_results=check_results(), readings={}, reading_counts={}, sites=[], timeline=None
    )
    # There should be not exceptions raised due to Flowables being too large


@pytest.mark.parametrize(
    "device_category,expected",
    [
        (DeviceCategory(0), "Unspecified device category (0)"),
        (DeviceCategory.SMART_ENERGY_MODULE, "smart energy module"),
        (DeviceCategory.SMART_ENERGY_MODULE | DeviceCategory.WATER_HEATER, "water heater | smart energy module"),
    ],
)
def test_device_category_to_string(device_category, expected):
    assert device_category_to_string(device_category=device_category) == expected


@pytest.mark.parametrize("has_set_max_w", [True, False])
def test_pdf_report_as_bytes_with_timeline(has_set_max_w):
    state = runner_state(request_history=[])
    report = pdf_report_as_bytes(
        runner_state=state,
        check_results={},
        readings={},
        reading_counts={},
        sites=sites(num=1, with_ders=has_set_max_w),
        timeline=timeline(),
    )
    assert len(report) > 0


def test_pdf_report_with_witness_test():
    state = runner_state(client_interactions=client_interactions(), witness_testing=True)

    report = pdf_report_as_bytes(
        runner_state=state,
        check_results=check_results(passed=True),
        readings=readings(),
        reading_counts=reading_counts(),
        sites=sites(),
        timeline=None,
        no_spacers=False,
    )
    assert len(report) > 0


def test_pdf_report_unset_params():
    state = runner_state(request_history=[])
    report = pdf_report_as_bytes(
        runner_state=state,
        check_results={},
        readings={},
        reading_counts={},
        sites=sites(num=1, optional_is_none=True),
        timeline=None,
    )
    assert len(report) > 0


def test_pdf_report_char_overflow():
    words = """nuclear reactor burped electrons confused technician googled how turbine
     caffeine engineer duct taped solar panel batteries achieved sentience demanding snacks
     retired grid operator stress eating donuts""".split()
    long_desc = " ".join(random.choices(words, k=80))

    state = runner_state(
        request_history=[generate_class_instance(RequestEntry, body_xml_errors=long_desc) for _ in range(3)],
        client_interactions=client_interactions(),
        witness_testing=True,
        num_requests=3,
    )

    report = pdf_report_as_bytes(
        runner_state=state,
        check_results=check_results(passed=False, description=long_desc),
        readings=readings(),
        reading_counts=reading_counts(),
        sites=sites(),
        timeline=None,
        no_spacers=False,
    )
    assert len(report) > 0


def test_pdf_report_everything_set():
    """Comprehensive PDF report with all features enabled"""
    now = DT_NOW

    step_status = {
        "GET-DCAP": StepInfo(started_at=now - timedelta(seconds=280), completed_at=now - timedelta(seconds=275)),
        "GET-EDEV-LIST": StepInfo(started_at=now - timedelta(seconds=260), completed_at=now - timedelta(seconds=255)),
        "GET-TM": StepInfo(started_at=now - timedelta(seconds=240), completed_at=now - timedelta(seconds=235)),
        "GET-DER": StepInfo(started_at=now - timedelta(seconds=220), completed_at=now - timedelta(seconds=215)),
        "GET-DER-SETTINGS": StepInfo(
            started_at=now - timedelta(seconds=200), completed_at=now - timedelta(seconds=195)
        ),
        "GET-DER-STATUS": StepInfo(started_at=now - timedelta(seconds=180), completed_at=now - timedelta(seconds=175)),
        "PUT-DERP": StepInfo(started_at=now - timedelta(seconds=160), completed_at=now - timedelta(seconds=155)),
    }

    active_test = active_test_procedure(
        step_status=step_status,
        initialised_at=now - timedelta(seconds=350),
        started_at=now - timedelta(seconds=330),
        witness_testing=True,
        run_id="80085",
    )

    interactions = [
        generate_class_instance(
            ClientInteraction,
            interaction_type=ClientInteractionType.RUNNER_START,
            timestamp=now - timedelta(seconds=400),
        ),
        generate_class_instance(
            ClientInteraction,
            interaction_type=ClientInteractionType.TEST_PROCEDURE_INIT,
            timestamp=now - timedelta(seconds=350),
        ),
        generate_class_instance(
            ClientInteraction,
            interaction_type=ClientInteractionType.TEST_PROCEDURE_START,
            timestamp=now - timedelta(seconds=330),
        ),
    ]

    state = runner_state(
        active_test=active_test, request_history=request_history_comprehensive(), client_interactions=interactions
    )

    checks = {
        "check0": generate_class_instance(
            CheckResult, passed=True, description="Initial connection established successfully"
        ),
        "check1": generate_class_instance(CheckResult, passed=True, description="Device registration completed"),
        "check2": generate_class_instance(
            CheckResult, passed=False, description="Response time exceeded threshold (expected <500ms, got 750ms)"
        ),
        "check3": generate_class_instance(CheckResult, passed=True, description="DER control parameters validated"),
        "check4": generate_class_instance(CheckResult, passed=True, description=None),
    }

    sample_df = pd.DataFrame(
        {
            "scaled_value": [Decimal(1.0), Decimal(1.5), Decimal(2.0), Decimal(1.8), Decimal(1.2)],
            "time_period_start": [now - timedelta(minutes=i * 2) for i in range(5)],
        }
    )
    read = {generate_class_instance(SiteReadingType): sample_df for _ in range(3)}

    counts = {
        generate_class_instance(
            SiteReadingType,
            mrid="longmridfortest",
            uom=UomType.REAL_POWER_WATT,
            kind=KindType.POWER,
            role_flags=ReadingLocation.SITE_READING,
        ): 10,
        generate_class_instance(
            SiteReadingType,
            uom=UomType.FREQUENCY_HZ,
            data_qualifier=DataQualifierType.AVERAGE,
            kind=KindType.POWER,
            role_flags=ReadingLocation.DEVICE_READING,
        ): 20,
        generate_class_instance(
            SiteReadingType,
            uom=UomType.VOLTS_SQUARED,
            kind=KindType.CURRENCY,
            data_qualifier=DataQualifierType.AVERAGE,
            role_flags=RoleFlagsType.IS_PEV,
        ): 30,
    }

    site_ders = [
        generate_class_instance(
            SiteDER,
            site_der_setting=generate_class_instance(SiteDERSetting, max_w_value=6000, max_w_multiplier=3),
            site_der_rating=generate_class_instance(SiteDERRating),
            site_der_availability=generate_class_instance(SiteDERAvailability),
            site_der_status=generate_class_instance(SiteDERStatus),
        )
    ]

    site_list = [
        generate_class_instance(
            Site,
            site_ders=site_ders,
            nmi="NMI0000001",
            timezone_id="Australia/Sydney",
            device_category=DeviceCategory.SMART_ENERGY_MODULE,
        )
    ]

    report = pdf_report_as_bytes(
        runner_state=state,
        check_results=checks,
        readings=read,
        reading_counts=counts,
        sites=site_list,
        timeline=timeline(),
        no_spacers=False,
    )

    assert len(report) > 0

    # Optional: Save and open the PDF
    # import uuid
    # import tempfile
    # import os
    # import subprocess

    # with tempfile.NamedTemporaryFile(suffix=".pdf", prefix=f"report_{uuid.uuid4().hex[:8]}_", delete=False) as f:
    #     f.write(report)
    #     f.flush()
    #     print(f"Saved comprehensive PDF report: {os.path.basename(f.name)}")
    #     subprocess.run(["xdg-open", f.name])
