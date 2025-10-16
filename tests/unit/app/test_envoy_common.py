from datetime import datetime
from decimal import Decimal

import pytest
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from envoy.server.model.archive.doe import (
    ArchiveDynamicOperatingEnvelope,
)
from envoy.server.model.site import Site, SiteDERSetting, SiteDER, DefaultSiteControl

from envoy.server.model.archive.site import ArchiveDefaultSiteControl
from envoy.server.model.doe import DynamicOperatingEnvelope, SiteControlGroup
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    KindType,
    RoleFlagsType,
    UomType,
)
import sqlalchemy

from cactus_runner.app.envoy_common import (
    ReadingLocation,
    get_active_site,
    get_csip_aus_site_reading_types,
    get_reading_counts_grouped_by_reading_type,
    get_site_controls_active_archived,
    get_site_defaults_with_archive,
    get_site_readings,
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
async def test_get_active_site_with_der_settings(pg_base_config):
    """Test that include_der_settings=True eagerly loads most recently changed SiteDER and settings"""
    async with generate_async_session(pg_base_config) as session:
        site1 = generate_class_instance(Site, seed=101, aggregator_id=1, site_id=1, changed_time=datetime(2022, 11, 10))
        site2 = generate_class_instance(Site, seed=202, aggregator_id=1, site_id=2, changed_time=datetime(2022, 11, 11))

        # Give site 2 der settings
        site_der = generate_class_instance(SiteDER, seed=301)
        site_der.site = site2  # Link to site2

        site_der_setting = generate_class_instance(SiteDERSetting, seed=401)
        site_der_setting.site_der = site_der  # Link to site_der

        session.add(site1)
        session.add(site2)
        await session.commit()

    # Test with include_der_settings=True
    async with generate_async_session(pg_base_config) as session:
        site = await get_active_site(session, include_der_settings=True)

        assert isinstance(site, Site)
        assert site.site_id == 2
        assert len(site.site_ders) > 0
        assert site.site_ders[0].site_der_setting is not None, "site_der_setting should be eagerly loaded"

    # Test with include_der_settings=False (default)
    async with generate_async_session(pg_base_config) as session:
        site = await get_active_site(session)
        assert isinstance(site, Site)
        assert site.site_id == 2
        with pytest.raises(sqlalchemy.exc.InvalidRequestError, match="is not available due to lazy='raise'"):
            _ = site.site_ders


@pytest.mark.anyio
async def test_get_csip_aus_site_reading_types_no_sites(pg_base_config):
    async with generate_async_session(pg_base_config) as session:
        result = await get_csip_aus_site_reading_types(
            session, UomType.REAL_POWER_WATT, ReadingLocation.SITE_READING, KindType.POWER, DataQualifierType.AVERAGE
        )
        assert_list_type(SiteReadingType, result, count=0)


@pytest.mark.anyio
async def test_get_csip_aus_site_reading_types_no_mups(pg_base_config):
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, seed=101, aggregator_id=1, site_id=1))
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        result = await get_csip_aus_site_reading_types(
            session, UomType.REAL_POWER_WATT, ReadingLocation.SITE_READING, KindType.POWER, DataQualifierType.AVERAGE
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
        # Storage extension
        session.add(
            generate_class_instance(
                SiteReadingType,
                seed=808,
                aggregator_id=1,
                site_reading_type_id=7,
                site=site2,
                uom=UomType.REAL_ENERGY_WATT_HOURS,
                data_qualifier=DataQualifierType.NOT_APPLICABLE,
                kind=KindType.ENERGY,
                role_flags=ReadingLocation.DEVICE_READING,
            )
        )  # Valid

        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        result = await get_csip_aus_site_reading_types(
            session=session,
            uom=UomType.REAL_POWER_WATT,
            location=ReadingLocation.DEVICE_READING,
            kind=KindType.POWER,
            qualifier=DataQualifierType.AVERAGE,
        )
        assert [2, 4] == [srt.site_reading_type_id for srt in result]
        assert_list_type(SiteReadingType, result, count=2)

        # Storage extension
        result = await get_csip_aus_site_reading_types(
            session=session,
            uom=UomType.REAL_ENERGY_WATT_HOURS,
            location=ReadingLocation.DEVICE_READING,
            kind=KindType.ENERGY,
            qualifier=DataQualifierType.NOT_APPLICABLE,
        )
        assert [7] == [srt.site_reading_type_id for srt in result]
        assert_list_type(SiteReadingType, result, count=1)


@pytest.mark.anyio
async def test_get_site_readings(pg_base_config):
    # Arrange
    async with generate_async_session(pg_base_config) as session:
        # Add active site
        site1 = generate_class_instance(Site, seed=101, aggregator_id=1, site_id=1)
        session.add(site1)

        # Add reading type
        power = generate_class_instance(
            SiteReadingType,
            seed=202,
            aggregator_id=1,
            site_reading_type_id=1,
            site=site1,
            uom=UomType.REAL_POWER_WATT,
            data_qualifier=DataQualifierType.AVERAGE,
            kind=KindType.POWER,
            role_flags=ReadingLocation.DEVICE_READING,
        )
        voltage = generate_class_instance(
            SiteReadingType,
            seed=303,
            aggregator_id=1,
            site_reading_type_id=2,
            site=site1,
            uom=UomType.VOLTAGE,
            data_qualifier=DataQualifierType.AVERAGE,
            kind=KindType.POWER,
            role_flags=ReadingLocation.DEVICE_READING,
        )
        energy = generate_class_instance(
            SiteReadingType,
            seed=404,
            aggregator_id=1,
            site_reading_type_id=3,
            site=site1,
            uom=UomType.REAL_ENERGY_WATT_HOURS,
            data_qualifier=DataQualifierType.NOT_APPLICABLE,
            kind=KindType.ENERGY,
            role_flags=ReadingLocation.DEVICE_READING,
        )
        session.add_all([power, voltage, energy])

        # Add readings
        def gen_sr(seed: int, srt: SiteReadingType) -> SiteReading:
            """Shorthand for generating a new SiteReading with the specified type"""
            return generate_class_instance(SiteReading, seed=seed, site_reading_type=srt)

        num_power_readings = 5
        power_readings = [gen_sr(i, power) for i in range(1, num_power_readings + 1)]
        session.add_all(power_readings)

        num_voltage_readings = 3
        voltage_readings = [gen_sr(i + num_power_readings, voltage) for i in range(1, num_voltage_readings + 1)]
        session.add_all(voltage_readings)

        num_energy_readings = 7
        energy_readings = [
            gen_sr(i + num_power_readings + num_voltage_readings, energy) for i in range(1, num_energy_readings + 1)
        ]
        session.add_all(energy_readings)

        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        power_type, *_ = await get_csip_aus_site_reading_types(
            session=session,
            uom=UomType.REAL_POWER_WATT,
            location=ReadingLocation.DEVICE_READING,
            kind=KindType.POWER,
            qualifier=DataQualifierType.AVERAGE,
        )

        power_readings = await get_site_readings(session=session, site_reading_type=power_type)
        assert_list_type(SiteReading, power_readings, count=num_power_readings)

        voltage_type, *_ = await get_csip_aus_site_reading_types(
            session=session,
            uom=UomType.VOLTAGE,
            location=ReadingLocation.DEVICE_READING,
            kind=KindType.POWER,
            qualifier=DataQualifierType.AVERAGE,
        )
        voltage_readings = await get_site_readings(session=session, site_reading_type=voltage_type)
        assert_list_type(SiteReading, voltage_readings, count=num_voltage_readings)

        energy_type, *_ = await get_csip_aus_site_reading_types(
            session=session,
            uom=UomType.REAL_ENERGY_WATT_HOURS,
            location=ReadingLocation.DEVICE_READING,
            kind=KindType.ENERGY,
            qualifier=DataQualifierType.NOT_APPLICABLE,
        )
        energy_readings = await get_site_readings(session=session, site_reading_type=energy_type)
        assert_list_type(SiteReading, energy_readings, count=num_energy_readings)


@pytest.mark.anyio
async def test_get_reading_counts_grouped_by_reading_type(pg_base_config):
    # Arrange
    async with generate_async_session(pg_base_config) as session:
        # Add active site
        site1 = generate_class_instance(Site, seed=101, aggregator_id=1, site_id=1)
        session.add(site1)

        # Add reading type
        power = generate_class_instance(
            SiteReadingType,
            seed=202,
            aggregator_id=1,
            site_reading_type_id=1,
            site=site1,
            uom=UomType.REAL_POWER_WATT,
            data_qualifier=DataQualifierType.AVERAGE,
            kind=KindType.POWER,
            role_flags=ReadingLocation.DEVICE_READING,
        )
        voltage = generate_class_instance(
            SiteReadingType,
            seed=303,
            aggregator_id=1,
            site_reading_type_id=2,
            site=site1,
            uom=UomType.VOLTAGE,
            data_qualifier=DataQualifierType.AVERAGE,
            kind=KindType.POWER,
            role_flags=ReadingLocation.DEVICE_READING,
        )
        energy = generate_class_instance(
            SiteReadingType,
            seed=404,
            aggregator_id=1,
            site_reading_type_id=3,
            site=site1,
            uom=UomType.REAL_ENERGY_WATT_HOURS,
            data_qualifier=DataQualifierType.NOT_APPLICABLE,
            kind=KindType.ENERGY,
            role_flags=ReadingLocation.DEVICE_READING,
        )
        session.add_all([power, voltage, energy])

        # Add readings
        def gen_sr(seed: int, srt: SiteReadingType) -> SiteReading:
            """Shorthand for generating a new SiteReading with the specified type"""
            return generate_class_instance(SiteReading, seed=seed, site_reading_type=srt)

        num_power_readings = 5
        power_readings = [gen_sr(i, power) for i in range(1, num_power_readings + 1)]
        session.add_all(power_readings)

        num_voltage_readings = 3
        voltage_readings = [gen_sr(i + num_power_readings, voltage) for i in range(1, num_voltage_readings + 1)]
        session.add_all(voltage_readings)

        num_energy_readings = 7
        energy_readings = [
            gen_sr(i + num_power_readings + num_voltage_readings, energy) for i in range(1, num_energy_readings + 1)
        ]
        session.add_all(energy_readings)

        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        power_type, *_ = await get_csip_aus_site_reading_types(
            session=session,
            uom=UomType.REAL_POWER_WATT,
            location=ReadingLocation.DEVICE_READING,
            kind=KindType.POWER,
            qualifier=DataQualifierType.AVERAGE,
        )
        voltage_type, *_ = await get_csip_aus_site_reading_types(
            session=session,
            uom=UomType.VOLTAGE,
            location=ReadingLocation.DEVICE_READING,
            kind=KindType.POWER,
            qualifier=DataQualifierType.AVERAGE,
        )
        energy_type, *_ = await get_csip_aus_site_reading_types(
            session=session,
            uom=UomType.REAL_ENERGY_WATT_HOURS,
            location=ReadingLocation.DEVICE_READING,
            kind=KindType.ENERGY,
            qualifier=DataQualifierType.NOT_APPLICABLE,
        )
        count_by_reading_type = await get_reading_counts_grouped_by_reading_type(session)

        assert len(count_by_reading_type) == 3  # three reading types (voltage, power and energy)
        assert count_by_reading_type[power_type] == num_power_readings
        assert count_by_reading_type[voltage_type] == num_voltage_readings
        assert count_by_reading_type[energy_type] == num_energy_readings


@pytest.mark.anyio
async def test_get_site_defaults_with_archive_empty_db(pg_empty_config):
    async with generate_async_session(pg_empty_config) as session:
        result = await get_site_defaults_with_archive(session)
        assert isinstance(result, list)
        assert len(result) == 0


@pytest.mark.anyio
async def test_get_site_defaults_with_archive(pg_base_config):
    """Really simple test - can get_site_defaults_with_archive fetch all active/archive site defaults"""
    # Arrange
    async with generate_async_session(pg_base_config) as session:
        site1 = generate_class_instance(Site, seed=101, aggregator_id=1, site_id=1)
        session.add(site1)

        session.add(generate_class_instance(DefaultSiteControl, seed=101, site=site1, ramp_rate_percent_per_second=1))
        session.add(generate_class_instance(DefaultSiteControl, seed=202, site=site1, ramp_rate_percent_per_second=2))
        session.add(
            generate_class_instance(
                ArchiveDefaultSiteControl, seed=303, site_id=1, optional_is_none=True, ramp_rate_percent_per_second=3
            )
        )
        session.add(
            generate_class_instance(ArchiveDefaultSiteControl, seed=404, site_id=1, ramp_rate_percent_per_second=4)
        )
        session.add(
            generate_class_instance(ArchiveDefaultSiteControl, seed=505, site_id=2, ramp_rate_percent_per_second=5)
        )

        await session.commit()

    # Act / Assert
    async with generate_async_session(pg_base_config) as session:
        result = await get_site_defaults_with_archive(session)
        assert isinstance(result, list)
        assert len(result) == 4

        assert (
            len(
                list(
                    filter(
                        lambda sc: isinstance(sc, DefaultSiteControl) and sc.ramp_rate_percent_per_second == 1, result
                    )
                )
            )
            == 1
        )
        assert (
            len(
                list(
                    filter(
                        lambda sc: isinstance(sc, DefaultSiteControl) and sc.ramp_rate_percent_per_second == 2, result
                    )
                )
            )
            == 1
        )
        assert (
            len(
                list(
                    filter(
                        lambda sc: isinstance(sc, ArchiveDefaultSiteControl) and sc.ramp_rate_percent_per_second == 3,
                        result,
                    )
                )
            )
            == 1
        )
        assert (
            len(
                list(
                    filter(
                        lambda sc: isinstance(sc, ArchiveDefaultSiteControl) and sc.ramp_rate_percent_per_second == 4,
                        result,
                    )
                )
            )
            == 1
        )


@pytest.mark.anyio
async def test_get_site_controls_active_archived_empty_db(pg_empty_config):
    async with generate_async_session(pg_empty_config) as session:
        result = await get_site_controls_active_archived(session)
        assert isinstance(result, list)
        assert len(result) == 0


@pytest.mark.anyio
async def test_get_site_controls_active_archived(pg_base_config):
    """Really simple test - can get_site_controls_active_archived fetch all active/archive controls for a site"""
    # Arrange
    async with generate_async_session(pg_base_config) as session:
        # Add active site
        site1 = generate_class_instance(Site, seed=101, aggregator_id=1, site_id=1)
        session.add(site1)

        session.add(
            generate_class_instance(
                SiteControlGroup,
                site_control_group_id=1,
                dynamic_operating_envelopes=[
                    generate_class_instance(
                        DynamicOperatingEnvelope,
                        seed=101,
                        import_limit_active_watts=Decimal("1.11"),
                        site=site1,
                        calculation_log_id=None,
                    ),
                    generate_class_instance(
                        DynamicOperatingEnvelope,
                        seed=202,
                        import_limit_active_watts=Decimal("2.22"),
                        site=site1,
                        calculation_log_id=None,
                    ),
                    generate_class_instance(
                        DynamicOperatingEnvelope,
                        seed=303,
                        import_limit_active_watts=Decimal("3.33"),
                        site=site1,
                        calculation_log_id=None,
                    ),
                ],
            )
        )

        session.add(
            generate_class_instance(
                ArchiveDynamicOperatingEnvelope,
                seed=404,
                deleted_time=None,
                site_id=1,
                import_limit_active_watts=Decimal("4.44"),
            )
        )
        session.add(
            generate_class_instance(
                ArchiveDynamicOperatingEnvelope, seed=505, site_id=1, import_limit_active_watts=Decimal("5.55")
            )
        )
        await session.commit()

    # Act / Assert
    async with generate_async_session(pg_base_config) as session:
        result = await get_site_controls_active_archived(session)
        assert isinstance(result, list)
        assert len(result) == 5

        assert (
            len(
                list(
                    filter(
                        lambda sc: isinstance(sc, DynamicOperatingEnvelope)
                        and sc.import_limit_active_watts == Decimal("1.11"),
                        result,
                    )
                )
            )
            == 1
        )
        assert (
            len(
                list(
                    filter(
                        lambda sc: isinstance(sc, DynamicOperatingEnvelope)
                        and sc.import_limit_active_watts == Decimal("2.22"),
                        result,
                    )
                )
            )
            == 1
        )
        assert (
            len(
                list(
                    filter(
                        lambda sc: isinstance(sc, DynamicOperatingEnvelope)
                        and sc.import_limit_active_watts == Decimal("3.33"),
                        result,
                    )
                )
            )
            == 1
        )
        assert (
            len(
                list(
                    filter(
                        lambda sc: isinstance(sc, ArchiveDynamicOperatingEnvelope)
                        and sc.import_limit_active_watts == Decimal("4.44"),
                        result,
                    )
                )
            )
            == 1
        )
        assert (
            len(
                list(
                    filter(
                        lambda sc: isinstance(sc, ArchiveDynamicOperatingEnvelope)
                        and sc.import_limit_active_watts == Decimal("5.55"),
                        result,
                    )
                )
            )
            == 1
        )
