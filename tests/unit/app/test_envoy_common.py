from datetime import datetime

import pytest
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from envoy.server.model.site import Site
from envoy.server.model.site_reading import SiteReadingType
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    KindType,
    RoleFlagsType,
    UomType,
)

from cactus_runner.app.envoy_common import (
    ReadingLocation,
    get_active_site,
    get_csip_aus_site_reading_types,
)


@pytest.mark.anyio
async def test_get_active_site_no_site(pg_base_config):
    async with generate_async_session(pg_base_config) as session:
        assert (await get_active_site(session)) is None


@pytest.mark.anyio
async def test_get_active_site_many_sites(pg_base_config):
    async with generate_async_session(pg_base_config) as session:
        session.add(
            generate_class_instance(Site, seed=101, aggregator_id=1, site_id=1, changed_time=datetime(2022, 11, 10))
        )
        session.add(
            generate_class_instance(Site, seed=202, aggregator_id=1, site_id=2, changed_time=datetime(2022, 11, 11))
        )
        session.add(
            generate_class_instance(Site, seed=303, aggregator_id=1, site_id=3, changed_time=datetime(2000, 11, 10))
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        site = await get_active_site(session)
        assert isinstance(site, Site)
        assert site.site_id == 2, "This is the most recently changed site"


@pytest.mark.anyio
async def test_get_csip_aus_site_reading_types_no_sites(pg_base_config):
    async with generate_async_session(pg_base_config) as session:
        result = await get_csip_aus_site_reading_types(
            session, UomType.REAL_POWER_WATT, ReadingLocation.SITE_READING, DataQualifierType.AVERAGE
        )
        assert_list_type(SiteReadingType, result, count=0)


@pytest.mark.anyio
async def test_get_csip_aus_site_reading_types_no_mups(pg_base_config):
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, seed=101, aggregator_id=1, site_id=1))
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        result = await get_csip_aus_site_reading_types(
            session, UomType.REAL_POWER_WATT, ReadingLocation.SITE_READING, DataQualifierType.AVERAGE
        )
        assert_list_type(SiteReadingType, result, count=0)


@pytest.mark.anyio
async def test_get_csip_aus_site_reading_types_many_mups(pg_base_config):
    async with generate_async_session(pg_base_config) as session:
        site1 = generate_class_instance(Site, seed=101, aggregator_id=1, site_id=1)
        site2 = generate_class_instance(Site, seed=202, aggregator_id=1, site_id=2)  # Active site
        session.add_all([site1, site2])

        session.add(
            generate_class_instance(
                SiteReadingType,
                seed=202,
                aggregator_id=1,
                site_reading_type_id=1,
                site=site2,
                uom=UomType.AMPERES_SQUARED,
                data_qualifier=DataQualifierType.AVERAGE,
                kind=KindType.POWER,
                role_flags=ReadingLocation.DEVICE_READING,
            )
        )  # Wrong uom
        session.add(
            generate_class_instance(
                SiteReadingType,
                seed=303,
                aggregator_id=1,
                site_reading_type_id=2,
                site=site2,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                kind=KindType.POWER,
                role_flags=ReadingLocation.DEVICE_READING,
            )
        )  # Valid
        session.add(
            generate_class_instance(
                SiteReadingType,
                seed=404,
                aggregator_id=1,
                site_reading_type_id=3,
                site=site2,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                kind=KindType.POWER,
                role_flags=RoleFlagsType.IS_REVENUE_QUALITY | RoleFlagsType.IS_MIRROR,
            )
        )  # Wrong role flags
        session.add(
            generate_class_instance(
                SiteReadingType,
                seed=505,
                aggregator_id=1,
                site_reading_type_id=4,
                site=site2,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                kind=KindType.POWER,
                role_flags=ReadingLocation.DEVICE_READING,
            )
        )  # Valid
        session.add(
            generate_class_instance(
                SiteReadingType,
                seed=606,
                aggregator_id=1,
                site_reading_type_id=5,
                site=site1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                kind=KindType.POWER,
                role_flags=ReadingLocation.DEVICE_READING,
            )
        )  # Wrong site id
        session.add(
            generate_class_instance(
                SiteReadingType,
                seed=707,
                aggregator_id=1,
                site_reading_type_id=6,
                site=site2,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                kind=KindType.POWER,
                role_flags=ReadingLocation.DEVICE_READING | RoleFlagsType.IS_DC,
            )
        )  # wrong role flags
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        result = await get_csip_aus_site_reading_types(
            session, UomType.REAL_POWER_WATT, ReadingLocation.DEVICE_READING, DataQualifierType.AVERAGE
        )
        assert [2, 4] == [srt.site_reading_type_id for srt in result]
        assert_list_type(SiteReadingType, result, count=2)
