from collections.abc import Sequence
from cactus_runner.plugin import dtos


def pow10_to_watts(value: int, pow_10: int) -> int:
    return int(value * pow(10, pow_10))

def reading_to_watts(srts: Sequence[dtos.SiteReadingType], r: dtos.SiteReading) -> int:
    for srt in srts:
        if srt.site_reading_type_id == r.site_reading_type_id:
            return pow10_to_watts(r.value, srt.power_of_ten_multiplier)

    raise ValueError(f"Couldn't find SiteReadingType with ID {r.site_reading_type_id}")
