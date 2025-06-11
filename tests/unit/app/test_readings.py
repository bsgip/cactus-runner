import pytest
from assertical.asserts.type import assert_dict_type
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from envoy.server.model.site import Site
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    KindType,
    UomType,
)
from pandas import DataFrame

from cactus_runner.app.envoy_common import (
    ReadingLocation,
)
from cactus_runner.app.readings import ReadingSpecifier, get_readings


@pytest.mark.asyncio
async def test_get_readings(mocker, pg_base_config):
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
            role_flags=ReadingLocation.SITE_READING,
        )
        session.add_all([power, voltage])

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

        await session.commit()

    session = generate_async_session(pg_base_config)
    mock_begin_session = mocker.patch("cactus_runner.app.handler.begin_session")
    mock_begin_session.__aenter__.return_value = session

    reading_specifiers = [
        ReadingSpecifier(uom=UomType.REAL_POWER_WATT, location=ReadingLocation.DEVICE_READING),
        ReadingSpecifier(uom=UomType.VOLTAGE, location=ReadingLocation.SITE_READING),
    ]

    # Act
    # async with generate_async_session(pg_base_config) as session:
    readings_map = await get_readings(reading_specifiers=reading_specifiers)

    # Assert
    assert_dict_type(SiteReadingType, DataFrame, readings_map, count=2)  # two reading types (voltage and power)
    assert [num_power_readings, num_voltage_readings] == [len(readings) for readings in readings_map.values()]
