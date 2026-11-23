from datetime import UTC, datetime

import pytest
from assertical.fake.generator import generate_class_instance
from envoy.server.model import Site

from cactus_runner.plugin import dtos
from cactus_runner.plugin.backends.envoy.mappers import (
    map_dto_site_write_to_envoy,
    map_envoy_site_to_dto,
)


@pytest.mark.parametrize(
    "nmi, expected_nmi, expected_checksum",
    [
        ("1234567890A", "1234567890", "A"),
        ("1234567890", "1234567890", None),
        ("123", "123", None),
        (None, None, None),
    ],
)
def test_map_site_nmi_roundtrip(nmi: str | None, expected_nmi: str | None, expected_checksum: str | None):
    site_write = generate_class_instance(
        dtos.SiteWrite,
        nmi=nmi,
        aggregator_id="1",
        created_time=datetime(2024, 1, 1, tzinfo=UTC),
        changed_time=datetime(2024, 1, 2, tzinfo=UTC),
    )

    envoy_site = map_dto_site_write_to_envoy(site_write)
    assert isinstance(envoy_site, Site)
    assert envoy_site.nmi == expected_nmi
    assert envoy_site.nmi_checksum == expected_checksum

    envoy_site.site_id = 1
    site_dto = map_envoy_site_to_dto(envoy_site)
    assert isinstance(site_dto, dtos.Site)
    assert site_dto.nmi == nmi
    assert site_dto.lfdi == site_write.lfdi
    assert site_dto.sfdi == site_write.sfdi
    assert site_dto.device_category == site_write.device_category


@pytest.mark.parametrize(
    "nmi, nmi_checksum, expected",
    [
        ("1234567890", "A", "1234567890A"),
        ("1234567890", None, "1234567890"),
        (None, None, None),
    ],
)
def test_map_envoy_site_to_dto_nmi(nmi: str | None, nmi_checksum: str | None, expected: str | None):
    site = generate_class_instance(Site, site_id=1, nmi=nmi, nmi_checksum=nmi_checksum)
    assert map_envoy_site_to_dto(site).nmi == expected
