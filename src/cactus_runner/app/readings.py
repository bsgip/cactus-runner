from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy_schema.server.schema.sep2.types import UomType
from pandas import DataFrame

from cactus_runner.app.database import (
    begin_session,
)
from cactus_runner.app.envoy_common import (
    ReadingLocation,
    get_csip_aus_site_reading_types,
    get_reading_counts_grouped_by_reading_type,
    get_site_readings,
)


@dataclass
class ReadingSpecifier:
    uom: UomType
    location: ReadingLocation


MANDATORY_READING_SPECIFIERS = [
    ReadingSpecifier(uom=UomType.VOLTAGE, location=ReadingLocation.SITE_READING),
    ReadingSpecifier(uom=UomType.REAL_POWER_WATT, location=ReadingLocation.SITE_READING),
    ReadingSpecifier(uom=UomType.REACTIVE_POWER_VAR, location=ReadingLocation.SITE_READING),
    ReadingSpecifier(uom=UomType.VOLTAGE, location=ReadingLocation.DEVICE_READING),
    ReadingSpecifier(uom=UomType.REAL_POWER_WATT, location=ReadingLocation.DEVICE_READING),
    ReadingSpecifier(uom=UomType.REACTIVE_POWER_VAR, location=ReadingLocation.DEVICE_READING),
]


async def get_readings(reading_specifiers: list[ReadingSpecifier]) -> dict[SiteReadingType, DataFrame]:
    readings = {}
    async with begin_session() as session:
        for reading_specifier in reading_specifiers:
            # There maybe more than one reading type per reading specifier, for example, for different phases
            reading_types = await get_csip_aus_site_reading_types(
                session=session, uom=reading_specifier.uom, location=reading_specifier.location
            )
            for reading_type in reading_types:
                reading_data = await get_site_readings(session=session, site_reading_type=reading_type)

                readings[reading_type] = process_readings(reading_type=reading_type, readings=reading_data)

    return readings


def process_readings(reading_type: SiteReadingType, readings: Sequence[SiteReading]) -> DataFrame:
    # Convert list of readings into a dataframe
    df = DataFrame([reading.__dict__ for reading in readings])

    # Calculate value with proper scaling applied (power_10)
    scale_factor = Decimal(10**reading_type.power_of_ten_multiplier)
    df["scaled_value"] = df["value"] * scale_factor

    return df


async def get_reading_counts() -> dict[SiteReadingType, int]:
    reading_counts = {}
    async with begin_session() as session:
        reading_counts = await get_reading_counts_grouped_by_reading_type(session=session)
    return reading_counts
