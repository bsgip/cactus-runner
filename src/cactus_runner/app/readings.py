from dataclasses import dataclass

from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy_schema.server.schema.sep2.types import UomType

from cactus_runner.app.database import (
    begin_session,
)
from cactus_runner.app.envoy_common import (
    ReadingLocation,
    get_csip_aus_site_reading_types,
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


async def get_readings(
    reading_specifiers: list[ReadingSpecifier] = MANDATORY_READING_SPECIFIERS,
) -> dict[SiteReadingType, list[SiteReading]]:
    readings = {}
    async with begin_session() as session:
        for reading_specifier in reading_specifiers:
            # There maybe more than one reading type per reading specifier, for example, for different phases
            reading_types = await get_csip_aus_site_reading_types(
                session=session, uom=reading_specifier.uom, location=reading_specifier.location
            )
            for reading_type in reading_types:
                reading_data = await get_site_readings(session=session, site_reading_type=reading_type)
                readings[reading_type] = reading_data

    return readings
