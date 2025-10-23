from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import chain
from typing import Any, Callable, Sequence, cast

from envoy.server.model.archive import ArchiveBase
from envoy.server.model.archive.doe import (
    ArchiveDynamicOperatingEnvelope,
)
from envoy.server.model.archive.site import ArchiveDefaultSiteControl
from envoy.server.model.doe import DynamicOperatingEnvelope
from envoy.server.model.site import DefaultSiteControl
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    KindType,
    UomType,
)
from intervaltree import Interval, IntervalTree  # type: ignore
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.envoy_common import (
    ReadingLocation,
    get_csip_aus_site_reading_types,
    get_site_controls_active_archived,
    get_site_defaults_with_archive,
    get_site_readings,
)


@dataclass
class TimelineDataStream:

    label: str  # Descriptive label of this data stream
    offset_watt_values: list[
        int | None
    ]  # The watt readings with the Nth entry being at Timeline.start + N * Timeline.interval_seconds
    stepped: bool  # If True - this data should be presented as a stepped line chart
    dashed: bool  # If True - this data should be a dashed line


@dataclass
class Timeline:
    """Represents a series of regular "power" observations aligned on interval_seconds offsets relative to start"""

    start: datetime  # The basis time
    interval_seconds: int  # The length of each regular interval within the timeline
    data_streams: list[TimelineDataStream]


def duration_to_label(duration_seconds: int) -> str:
    """Simple method for turning a duration to a simple text label. eg 123 becomes '2m3s'"""
    if duration_seconds == 0:
        return "start"

    abs_duration = abs(duration_seconds)
    if abs_duration < 60:
        return f"{duration_seconds}s"

    mins = abs_duration // 60
    seconds = abs_duration % 60
    sign = "-" if duration_seconds < 0 else ""
    if seconds == 0:
        return f"{sign}{mins}m"
    else:
        return f"{sign}{mins}m{seconds}s"


def decimal_to_watts(value: Decimal | None, negate: bool) -> int | None:
    if value is None:
        return None

    return (-1 * int(value)) if negate else int(value)


def pow10_to_watts(value: int, pow_10: int) -> int:
    return int(value * pow(10, pow_10))


def reading_to_watts(srts: Sequence[SiteReadingType], r: SiteReading) -> int:
    for srt in srts:
        if srt.site_reading_type_id == r.site_reading_type_id:
            return pow10_to_watts(r.value, srt.power_of_ten_multiplier)

    raise ValueError(f"Couldn't find SiteReadingType with ID {r.site_reading_type_id}")


def entity_to_priority(entity: Any) -> int:
    """this function will calculate the entity priority which follows these rules:

    1) an ArchiveBase (deletion) descendent is ALWAYS lower priority when compared to other types.
        * Tiebreaks are handled on changed_time (which should be present for all types we care about)
    2) a "regular" non archived row
        * Tiebreaks are handled on changed_time (which should be present for all types we care about)
    3) an ArchiveBase (non deletion) descendent is highest priority ONLY from its start time to archive time (otherwise
       it can be ignored)
        * Tiebreaks here are done by taking the highest archive time"""
    if isinstance(entity, ArchiveBase):
        if entity.deleted_time is None:
            return 3  # Archive records
        else:
            return 1  # Deleted records
    else:
        return 2  # normal record


def highest_priority_entity(entities: set[Interval]) -> Any:
    """this function will take the highest priority entity which follows these priorities:

    This priority mapping is done by entity_to_priority
    """
    highest_entity: Any | None = None
    highest_priority: int = -1

    for e in entities:
        current_entity = e.data
        current_entity_priority = entity_to_priority(current_entity)
        if current_entity_priority > highest_priority:
            highest_entity = current_entity
            highest_priority = current_entity_priority
            continue

        if current_entity_priority < highest_priority:
            continue

        # At this point - we've got something at the same priority - we need to tiebreak
        if (
            isinstance(current_entity, ArchiveBase)
            and current_entity.deleted_time is None
            and isinstance(highest_entity, ArchiveBase)
            and current_entity.deleted_time is None
        ):
            # if we have two archive records (not deletion records) - Take the one with the higher archive time
            if (
                current_entity.archive_time
                and highest_entity.archive_time
                and current_entity.archive_time > highest_entity.archive_time
            ):
                highest_entity = current_entity
                continue
        else:
            # For all other cases - we use changed time as the tiebreaker
            # Tiebreak on changed_time
            if getattr(current_entity, "changed_time", datetime.min) > getattr(
                highest_entity, "changed_time", datetime.min
            ):
                highest_entity = current_entity
                continue

    if highest_entity is None:
        raise ValueError("entities is empty")
    return highest_entity


def generate_offset_watt_values(
    tree: IntervalTree,
    start: datetime,
    end: datetime,
    interval_seconds: int,
    watt_fetchers: list[Callable[[Any], int | None]],
) -> list[list[int | None]]:
    """Interrogates tree at regular intervals from start -> end at regular intervals of interval_seconds. At each
    of those intervals (if an entity is returned) - call each of the watt_fetcher Callables on that entity. The results
    of these calls will be written to the output lists.

    If multiple entities are returned for a given interval - uses highest_priority_entity to break the tie.

    returns: A list of discovered watt values for EACH watt_fetchers entry (they will 1-1 correspond)"""
    current_interval: datetime = start
    delta = timedelta(seconds=interval_seconds)

    fetched_data: list[list[int | None]] = [[] for _ in watt_fetchers]  # Populate with empty lists

    while current_interval < end:
        next_interval = current_interval + delta
        matching_intervals: set[Interval] = tree[current_interval:next_interval]  # type: ignore
        if matching_intervals:
            entity = highest_priority_entity(matching_intervals)
            for watt_data, fetcher in zip(fetched_data, watt_fetchers):
                watt_data.append(fetcher(entity))
        else:
            for watt_data in fetched_data:
                watt_data.append(None)

        current_interval = next_interval

    return fetched_data


async def generate_readings_data_stream(
    session: AsyncSession, label: str, location: ReadingLocation, start: datetime, end: datetime, interval_seconds: int
) -> TimelineDataStream:
    srts = await get_csip_aus_site_reading_types(
        session, UomType.REAL_POWER_WATT, location, KindType.POWER, DataQualifierType.AVERAGE
    )

    # Build the interval tree from all matched readings
    tree = IntervalTree()
    for srt in srts:
        # Dump all readings - we will refine in memory
        # (we could be fancy and try to interrogate records within the start/end range but that introduces a bit more
        # complexity and we should only be dealing with < 60 records)
        readings = await get_site_readings(session, srt)
        tree.update(
            (
                Interval(r.time_period_start, r.time_period_start + timedelta(seconds=r.time_period_seconds), r)
                for r in readings
            )
        )

    # Generate all the reading data
    offset_watt_values = generate_offset_watt_values(
        tree, start, end, interval_seconds, [lambda e: reading_to_watts(srts, e)]
    )[0]

    return TimelineDataStream(label=label, offset_watt_values=offset_watt_values, stepped=False, dashed=False)


async def generate_control_data_streams(
    session: AsyncSession, start: datetime, end: datetime, interval_seconds: int
) -> list[TimelineDataStream]:

    all_controls = await get_site_controls_active_archived(session)
    site_control_group_ids: set[int] = set((c.site_control_group_id for c in all_controls))
    all_data_streams: list[TimelineDataStream] = []

    # We will enumerate all the controls, batched by the site control group (DERProgram) that they belong to
    for site_control_group_id in sorted(site_control_group_ids):
        # Build the interval tree from all the controls we found (underneath this SiteControlGroup)
        intervals: list[Interval] = []
        for control in all_controls:
            if control.site_control_group_id != site_control_group_id:
                continue

            # Don't render any superseded controls - we will instead show the archive values for when it WASN'T
            # superseded (if applicable)
            if control.superseded:
                continue

            end_time = control.start_time + timedelta(seconds=control.duration_seconds)
            if isinstance(control, ArchiveDynamicOperatingEnvelope):
                # If this is a deleted control we use a slightly different interval - we only report on start time until
                # the deletion time
                if control.deleted_time is not None and control.deleted_time > control.start_time:
                    end_time = min(control.deleted_time, end_time)  # In case the control was deleted AFTER it finished
                    intervals.append(Interval(control.start_time, end_time, control))

                # If this is an archive control (non deletion) we use a slightly different interval - we only report
                # on start time until the archive time (the moment the values stopped being relevant)
                if control.archive_time is not None and control.archive_time > control.start_time:
                    end_time = min(control.archive_time, end_time)  # In case the control was archived AFTER it finished
                    intervals.append(Interval(control.start_time, end_time, control))

            else:
                # For regular controls - we can just take the start / end times as the time that's valid
                intervals.append(Interval(control.start_time, end_time, control))

        if len(intervals) == 0:
            continue
        tree = IntervalTree(intervals)

        # Generate all the reading data
        offset_watt_values = generate_offset_watt_values(
            tree,
            start,
            end,
            interval_seconds,
            [
                lambda e: decimal_to_watts(cast(DynamicOperatingEnvelope, e).import_limit_active_watts, False),
                lambda e: decimal_to_watts(cast(DynamicOperatingEnvelope, e).export_limit_watts, True),
                lambda e: decimal_to_watts(cast(DynamicOperatingEnvelope, e).load_limit_active_watts, False),
                lambda e: decimal_to_watts(cast(DynamicOperatingEnvelope, e).generation_limit_active_watts, True),
            ],
        )

        # These indexes correspond 1-1 with the lambda's above
        all_data_streams.extend(
            [
                TimelineDataStream(
                    label=f"/derp/{site_control_group_id} opModImpLimW",
                    offset_watt_values=offset_watt_values[0],
                    stepped=True,
                    dashed=False,
                ),
                TimelineDataStream(
                    label=f"/derp/{site_control_group_id} opModExpLimW",
                    offset_watt_values=offset_watt_values[1],
                    stepped=True,
                    dashed=False,
                ),
                TimelineDataStream(
                    label=f"/derp/{site_control_group_id} opModLoadLimW",
                    offset_watt_values=offset_watt_values[2],
                    stepped=True,
                    dashed=False,
                ),
                TimelineDataStream(
                    label=f"/derp/{site_control_group_id} opModGenLimW",
                    offset_watt_values=offset_watt_values[3],
                    stepped=True,
                    dashed=False,
                ),
            ]
        )

    return all_data_streams


async def generate_default_control_data_streams(
    session: AsyncSession, start: datetime, end: datetime, interval_seconds: int
) -> list[TimelineDataStream]:
    all_defaults = await get_site_defaults_with_archive(session)

    intervals: list[Interval] = []
    for default_control in all_defaults:

        if isinstance(default_control, ArchiveDefaultSiteControl):
            # An archive record was active only from when it was last changed and then archived
            start_time = default_control.changed_time
            end_time = default_control.archive_time
        else:
            # An active record is active from when it was last updated to infinity
            start_time = default_control.changed_time
            end_time = datetime(9999, 1, 1, tzinfo=start.tzinfo)  # Suitably long time in the future that won't overflow

        intervals.append(Interval(start_time, end_time, default_control))

    if len(intervals) == 0:
        return []
    tree = IntervalTree(intervals)

    # Generate all the reading data
    offset_watt_values = generate_offset_watt_values(
        tree,
        start,
        end,
        interval_seconds,
        [
            lambda e: decimal_to_watts(cast(DefaultSiteControl, e).import_limit_active_watts, False),
            lambda e: decimal_to_watts(cast(DefaultSiteControl, e).export_limit_active_watts, True),
            lambda e: decimal_to_watts(cast(DefaultSiteControl, e).load_limit_active_watts, False),
            lambda e: decimal_to_watts(cast(DefaultSiteControl, e).generation_limit_active_watts, True),
        ],
    )

    # These indexes correspond 1-1 with the lambda's above
    return [
        TimelineDataStream(
            label="Default opModImpLimW",
            offset_watt_values=offset_watt_values[0],
            stepped=True,
            dashed=True,
        ),
        TimelineDataStream(
            label="Default opModExpLimW",
            offset_watt_values=offset_watt_values[1],
            stepped=True,
            dashed=True,
        ),
        TimelineDataStream(
            label="Default opModLoadLimW",
            offset_watt_values=offset_watt_values[2],
            stepped=True,
            dashed=True,
        ),
        TimelineDataStream(
            label="Default opModGenLimW",
            offset_watt_values=offset_watt_values[3],
            stepped=True,
            dashed=True,
        ),
    ]


async def generate_timeline(
    session: AsyncSession, start: datetime, interval_seconds: int, end: datetime | None = None
) -> Timeline:
    """Constructs a Timeline from the database"""

    if end is None:
        end = datetime.now(tz=start.tzinfo)

    site_readings = await generate_readings_data_stream(
        session,
        label="Site Power",
        location=ReadingLocation.SITE_READING,
        start=start,
        end=end,
        interval_seconds=interval_seconds,
    )
    device_readings = await generate_readings_data_stream(
        session,
        label="Device Power",
        location=ReadingLocation.DEVICE_READING,
        start=start,
        end=end,
        interval_seconds=interval_seconds,
    )
    controls = await generate_control_data_streams(session, start=start, end=end, interval_seconds=interval_seconds)
    defaults = await generate_default_control_data_streams(
        session, start=start, end=end, interval_seconds=interval_seconds
    )

    # Collate the data streams - culling any that don't have at least 1 value
    populated_data_streams = list(
        filter(
            lambda ds: any((v is not None for v in ds.offset_watt_values)),
            chain([site_readings, device_readings], controls, defaults),
        )
    )
    return Timeline(start, interval_seconds, populated_data_streams)
