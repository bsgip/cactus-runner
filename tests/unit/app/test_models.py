from datetime import datetime

from assertical.asserts.generator import assert_class_instance_equality
from assertical.fake.generator import generate_class_instance
from envoy.server.model import SiteReadingType
from envoy.server.model.site import Site as EnvoySite

from cactus_runner.models import ReadingType, Site, StepInfo, StepStatus


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
