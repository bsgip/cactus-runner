import itertools

import pytest
from assertical.asserts.type import assert_dict_type
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from envoy.server.model.site import Site
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy_schema.server.schema.sep2.types import (
    AccumulationBehaviourType,
    DataQualifierType,
    FlowDirectionType,
    KindType,
    PhaseCode,
    UomType,
)
from pandas import DataFrame

from cactus_runner.app.envoy_common import (
    ReadingLocation,
)
from cactus_runner.app.readings import (
    ReadingSpecifier,
    get_readings,
    group_reading_types,
    reading_types_equivalent,
)


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
    assert sorted([num_power_readings, num_voltage_readings]) == sorted(
        [len(readings) for readings in readings_map.values()]
    )


@pytest.mark.parametrize(
    "rt1,rt2,expected_result",
    [
        (
            generate_class_instance(
                SiteReadingType,
                seed=1,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            generate_class_instance(
                SiteReadingType,
                seed=2,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            True,
        ),
        (
            generate_class_instance(
                SiteReadingType,
                seed=1,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            generate_class_instance(
                SiteReadingType,
                seed=2,
                aggregator_id=2,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            False,  # different aggregator
        ),
        (
            generate_class_instance(
                SiteReadingType,
                seed=1,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            generate_class_instance(
                SiteReadingType,
                seed=2,
                aggregator_id=1,
                site_id=2,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            False,  # different site id
        ),
        (
            generate_class_instance(
                SiteReadingType,
                seed=1,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            generate_class_instance(
                SiteReadingType,
                seed=2,
                aggregator_id=1,
                site_id=1,
                uom=UomType.VOLTAGE,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            False,  # different uom
        ),
        (
            generate_class_instance(
                SiteReadingType,
                seed=1,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            generate_class_instance(
                SiteReadingType,
                seed=2,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.MAXIMUM,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            False,  # different data qualifier
        ),
        (
            generate_class_instance(
                SiteReadingType,
                seed=1,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            generate_class_instance(
                SiteReadingType,
                seed=2,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.REVERSE,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            False,  # different flow direction
        ),
        (
            generate_class_instance(
                SiteReadingType,
                seed=1,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            generate_class_instance(
                SiteReadingType,
                seed=2,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.SUMMATION,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            False,  # different accumulation behaviour
        ),
        (
            generate_class_instance(
                SiteReadingType,
                seed=1,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            generate_class_instance(
                SiteReadingType,
                seed=2,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.ENERGY,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            False,  # different kind
        ),
        (
            generate_class_instance(
                SiteReadingType,
                seed=1,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            generate_class_instance(
                SiteReadingType,
                seed=2,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_B,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            False,  # different phase
        ),
        (
            generate_class_instance(
                SiteReadingType,
                seed=1,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.DEVICE_READING,
            ),
            generate_class_instance(
                SiteReadingType,
                seed=2,
                aggregator_id=1,
                site_id=1,
                uom=UomType.REAL_POWER_WATT,
                data_qualifier=DataQualifierType.AVERAGE,
                flow_direction=FlowDirectionType.FORWARD,
                accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                kind=KindType.POWER,
                phase=PhaseCode.PHASE_ABC,
                role_flags=ReadingLocation.SITE_READING,
            ),
            False,  # different role flags/location
        ),
    ],
)
def test_reading_types_equivalent(rt1, rt2, expected_result):
    assert reading_types_equivalent(rt1, rt2) == expected_result


@pytest.mark.parametrize(
    "reading_types, expected_group_indexes",
    [
        (
            [
                generate_class_instance(
                    SiteReadingType,
                    seed=1,
                    aggregator_id=1,
                    site_id=1,
                    uom=UomType.REAL_POWER_WATT,
                    data_qualifier=DataQualifierType.AVERAGE,
                    flow_direction=FlowDirectionType.FORWARD,
                    accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                    kind=KindType.POWER,
                    phase=PhaseCode.PHASE_ABC,
                    role_flags=ReadingLocation.DEVICE_READING,
                )
            ],
            [[0]],  # one group (only one reading type)
        ),
        (
            [
                generate_class_instance(
                    SiteReadingType,
                    seed=1,
                    aggregator_id=1,
                    site_id=1,
                    uom=UomType.REAL_POWER_WATT,
                    data_qualifier=DataQualifierType.AVERAGE,
                    flow_direction=FlowDirectionType.FORWARD,
                    accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                    kind=KindType.POWER,
                    phase=PhaseCode.PHASE_ABC,
                    role_flags=ReadingLocation.DEVICE_READING,
                ),
                generate_class_instance(
                    SiteReadingType,
                    seed=2,
                    aggregator_id=1,
                    site_id=1,
                    uom=UomType.CURRENT_AMPERES,
                    data_qualifier=DataQualifierType.AVERAGE,
                    flow_direction=FlowDirectionType.FORWARD,
                    accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                    kind=KindType.POWER,
                    phase=PhaseCode.PHASE_ABC,
                    role_flags=ReadingLocation.DEVICE_READING,
                ),
            ],
            [[0], [1]],  # Two groups (different uom)
        ),
        (
            [
                generate_class_instance(
                    SiteReadingType,
                    seed=1,
                    aggregator_id=1,
                    site_id=1,
                    uom=UomType.REAL_POWER_WATT,
                    data_qualifier=DataQualifierType.AVERAGE,
                    flow_direction=FlowDirectionType.FORWARD,
                    accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                    kind=KindType.POWER,
                    phase=PhaseCode.PHASE_ABC,
                    role_flags=ReadingLocation.DEVICE_READING,
                    power_of_ten_multiplier=1,
                ),
                generate_class_instance(
                    SiteReadingType,
                    seed=2,
                    aggregator_id=1,
                    site_id=1,
                    uom=UomType.REAL_POWER_WATT,
                    data_qualifier=DataQualifierType.AVERAGE,
                    flow_direction=FlowDirectionType.FORWARD,
                    accumulation_behaviour=AccumulationBehaviourType.CUMULATIVE,
                    kind=KindType.POWER,
                    phase=PhaseCode.PHASE_ABC,
                    role_flags=ReadingLocation.DEVICE_READING,
                    power_of_ten_multiplier=2,
                ),
            ],
            [[0, 1]],  # One group (despite different power of ten multiplier)
        ),
    ],
)
def test_group_reading_types(reading_types, expected_group_indexes):
    def replace_index_by_reading_type(item, reading_types):
        if isinstance(item, list):
            return [replace_index_by_reading_type(x, reading_types) for x in item]
        else:
            return reading_types[item]

    # Arrange
    expected_groups = replace_index_by_reading_type(expected_group_indexes, reading_types)

    # Act
    groups = group_reading_types(reading_types=reading_types)

    # Assert
    flattened = list(itertools.chain(*groups))
    assert len(reading_types) == len(flattened)
    assert len(groups) == len(expected_groups)
    for expected_group in expected_groups:
        assert expected_group in groups
