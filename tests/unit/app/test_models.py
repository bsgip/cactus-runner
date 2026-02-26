import inspect
import sys
from datetime import datetime

from assertical.asserts.generator import assert_class_instance_equality
from assertical.fake.generator import (
    generate_class_instance,
)
from envoy.server.model import SiteReadingType
from envoy.server.model.site import Site as EnvoySite

from cactus_runner.models import (
    ReadingType,
    ReportingData,
    ReportingData_Base,
    ReportingData_v1,
    Site,
    StepInfo,
    StepStatus,
)


def test_step_info():

    step = StepInfo()
    assert step.get_step_status() == StepStatus.PENDING  # No dates set

    step.started_at = datetime.now()
    assert step.get_step_status() == StepStatus.ACTIVE  # Started but not completed

    step.completed_at = datetime.now()
    assert step.get_step_status() == StepStatus.RESOLVED  # Both dates set


def test_reading_type_from_site_reading_type():
    site_reading_type = generate_class_instance(SiteReadingType)

    reading_type = ReadingType.from_site_reading_type(site_reading_type)

    assert_class_instance_equality(ReadingType, reading_type, site_reading_type)


def test_reading_type_serialisation():

    reading_type = generate_class_instance(ReadingType)

    assert ReadingType.from_json(reading_type.to_json()) == reading_type
    assert ReadingType.from_dict(reading_type.to_dict()) == reading_type


def test_site_from_envoy_site():
    envoy_site = generate_class_instance(EnvoySite)

    site = Site.from_site(envoy_site)

    assert site.site_id == envoy_site.site_id
    assert site.nmi == envoy_site.nmi
    assert site.created_time == envoy_site.created_time
    assert site.device_category == envoy_site.device_category


def test_site_serialization():

    site = generate_class_instance(Site)

    assert Site.from_json(site.to_json()) == site
    assert Site.from_dict(site.to_dict()) == site


def test_reporting_data_versions():
    CLASS_NAME_PREFIX = "ReportingData_v"
    MODULE = "cactus_runner.models"

    # Determine all the different versions of ReportingData classes we have defined in MODULE
    reporting_data_classes = [
        cls
        for name, cls in inspect.getmembers(sys.modules[MODULE], inspect.isclass)
        if name.startswith(CLASS_NAME_PREFIX)
    ]

    # Perform checks on each version of a reporting data class
    for ReportingDataClass in reporting_data_classes:

        # All reporting classes must be subclasses of ReportingData_Base in order to receive the version attribute
        assert issubclass(ReportingDataClass, ReportingData_Base)

        # Check we can serialise and deserialise the reporting class
        expected_reporting_data = generate_class_instance(
            ReportingData_v1, optional_is_none=True, generate_relationships=True
        )
        json = expected_reporting_data.to_json()
        reporting_data = ReportingData.from_json(expected_reporting_data.version, json)
        assert_class_instance_equality(ReportingDataClass, reporting_data, expected_reporting_data)
