from datetime import datetime

import pytest
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from envoy.server.model.site import Site
from envoy.server.model.site_reading import SiteReading, SiteReadingType
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
    get_reading_counts_grouped_by_reading_type,
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
