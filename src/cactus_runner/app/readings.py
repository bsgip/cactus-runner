from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

import pandas as pd
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy_schema.server.schema.sep2.types import DataQualifierType, KindType, UomType
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.envoy_common import (
    ReadingLocation,
    get_csip_aus_site_reading_types,
    get_site_readings,
)


@dataclass
class ReadingSpecifier:
    """A shorthand alternative to SiteReadingType"""

    uom: UomType
    location: ReadingLocation
    kind: KindType
    qualifier: DataQualifierType


# Defines the mandatory CSIP-AUS readings expected to be sent to a utility server
# See Annex A - Reporting DER Data (DER Monitoring Data) of the CSIP-AUS (Jan 2023) specification.
MANDATORY_READING_SPECIFIERS = [
    ReadingSpecifier(
        uom=UomType.VOLTAGE,
        location=ReadingLocation.SITE_READING,
        kind=KindType.POWER,
        qualifier=DataQualifierType.AVERAGE,
    ),
    ReadingSpecifier(
        uom=UomType.REAL_POWER_WATT,
        location=ReadingLocation.SITE_READING,
        kind=KindType.POWER,
        qualifier=DataQualifierType.AVERAGE,
    ),
    ReadingSpecifier(
        uom=UomType.REACTIVE_POWER_VAR,
        location=ReadingLocation.SITE_READING,
        kind=KindType.POWER,
        qualifier=DataQualifierType.AVERAGE,
    ),
    ReadingSpecifier(
        uom=UomType.VOLTAGE,
        location=ReadingLocation.DEVICE_READING,
        kind=KindType.POWER,
        qualifier=DataQualifierType.AVERAGE,
    ),
    ReadingSpecifier(
        uom=UomType.REAL_POWER_WATT,
        location=ReadingLocation.DEVICE_READING,
        kind=KindType.POWER,
        qualifier=DataQualifierType.AVERAGE,
    ),
    ReadingSpecifier(
        uom=UomType.REACTIVE_POWER_VAR,
        location=ReadingLocation.DEVICE_READING,
        kind=KindType.POWER,
        qualifier=DataQualifierType.AVERAGE,
    ),
    # Storage extension
    ReadingSpecifier(
        uom=UomType.REAL_ENERGY_WATT_HOURS,
        location=ReadingLocation.DEVICE_READING,
        kind=KindType.ENERGY,
        qualifier=DataQualifierType.NOT_APPLICABLE,
    ),
]


async def get_readings(
    session: AsyncSession, reading_specifiers: list[ReadingSpecifier]
) -> dict[SiteReadingType, pd.DataFrame]:
    """Returns a dataframe containing readings matching the 'reading_specifiers'. If no readings are present for the
    reading_specifier - it will NOT be included in the resulting dataframe.

    Args:
        reading_specifiers: A list of the types of readings to return.

    Returns:
        A dict of SiteReadingType mapped to a dataframe containing all the readings of that type. The dataframe
        contains all the attributes of a SiteReading along with extra column 'scaled_value'. The 'scaled_value'
        is the readings 'value' scaled by the SiteReadingType's power_of_10_multiplier.
    """
    readings = {}

    for reading_specifier in reading_specifiers:
        # There maybe more than one reading type per reading specifier, for example, for different phases
        reading_types = await get_csip_aus_site_reading_types(
            session=session,
            uom=reading_specifier.uom,
            location=reading_specifier.location,
            kind=reading_specifier.kind,
            qualifier=reading_specifier.qualifier,
        )
        for reading_type in reading_types:
            reading_data = await get_site_readings(session=session, site_reading_type=reading_type)

            if reading_data:
                readings[reading_type] = scale_readings(reading_type=reading_type, readings=reading_data)

    groups = group_reading_types(list(readings.keys()))
    merged_readings = merge_readings(readings=readings, groups=groups)

    return merged_readings


def merge_readings(
    readings: dict[SiteReadingType, pd.DataFrame], groups: list[list[SiteReadingType]]
) -> dict[SiteReadingType, pd.DataFrame]:
    """Merges the dataframes for reading types that have been grouped.

    Args:
        readings: A dict of SiteReadintType mapped to a dataframe containing readings of that type.
        groups: A list of groups. Each group is a list of SiteReadingTypes that need to be merged.

    Returns:
        A new readings dictionary mapping a representative SiteReadingType to a merged dataframe containing
        all the rows from dataframes in the same group. The first SiteReadingType in a group is selected as
        the representative SiteReadingType to key the new dictionary.
    """
    merged_readings = {}
    for group in groups:
        # We need to choose a SiteReadingType to represent all the merged values.
        # Here we choose the first SiteReadingType in the group.
        primary_key: SiteReadingType = group[0]
        merged = pd.concat([readings[reading_type] for reading_type in group])
        sorted = merged.sort_values(by=["time_period_start"])
        merged_readings[primary_key] = sorted

    return merged_readings


def reading_types_equivalent(rt1: SiteReadingType, rt2: SiteReadingType) -> bool:
    """Determines if two SiteReadingTypes are equivalent

    The purpose of this function is to find site reading types that represent
    the same quantity but only (effectively) differ by power_of_ten_multiplier

    Ignores the following attributes:
    - power_of_ten_multiplier
    - site_reading_type_id
    - default_interval_seconds
    - created_time
    - changed_time
    - site (already covered by site_id)

    Args:
        rt1: A SiteReadingType instance.
        rt2: Another SiteReadingType instance to compare to 'rt1'.

    Returns:
        bool: True is 'rt1' and 'rt2' are equivalent (see above) else False.
    """
    return (
        rt1.aggregator_id == rt2.aggregator_id
        and rt1.site_id == rt2.site_id
        and rt1.uom == rt2.uom
        and rt1.data_qualifier == rt2.data_qualifier
        and rt1.flow_direction == rt2.flow_direction
        and rt1.accumulation_behaviour == rt2.accumulation_behaviour
        and rt1.kind == rt2.kind
        and rt1.phase == rt2.phase
        and rt1.role_flags == rt2.role_flags
    )


def group_reading_types(reading_types: list[SiteReadingType]) -> list[list[SiteReadingType]]:
    """Groups equivalent reading types together.

    A group consists of a list of reading types that all represent the same physical quantity but only
    (effectively) differ in 'power_of_10_multiplier'.

    Args:
        reading_types: A list of SiteReadingTypes to group.

    Returns:
        A list of grouped SiteReadingTypes. Each group is list where the SiteReadingTypes are equivalent.
    """
    unprocessed_reading_types = reading_types.copy()

    grouped_reading_types: list[list[SiteReadingType]] = []
    while unprocessed_reading_types:
        current_reading_type = unprocessed_reading_types.pop(0)

        # Does current reading type match one in an existing group?
        added_to_existing_group = False
        for group in grouped_reading_types:
            if reading_types_equivalent(current_reading_type, group[0]):
                added_to_existing_group = True
                group.append(current_reading_type)
                break

        if not added_to_existing_group:
            grouped_reading_types.append([current_reading_type])

    return grouped_reading_types


def scale_readings(reading_type: SiteReadingType, readings: Sequence[SiteReading]) -> pd.DataFrame:
    """Converts the readings to dataframe and calculates scaled value (power of 10 multiplier). Requires readings to be
    a non empty list otherwise a ValueError will be raised

    Args:
        reading_type: The SiteReadingType associated with the 'readings'.
        readings: A sequence of SiteReadings to be scaled by the power of 10 multiplier of 'reading_type'.

    Returns:
        DataFrame: A dataframe containing all the readings in 'readings' along with an extra column
        'scaled_value'. 'scaled_value' = 'value' * power_of_10_multiplier.
    """
    if not readings:
        raise ValueError("Expected at least 1 entry in readings. Got 0/None")

    # Convert list of readings into a dataframe
    df = pd.DataFrame([reading.__dict__ for reading in readings])

    # Calculate value with proper scaling applied (power_10)
    scale_factor = Decimal(10**reading_type.power_of_ten_multiplier)
    df["scaled_value"] = df["value"] * scale_factor  # type: ignore

    return df
