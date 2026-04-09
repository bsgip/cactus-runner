"""
Generates a standalone HTML chart of expected device active power limits
throughout a test run.

The chart shows two traces:
  - Upper bound: effective export / generation limit (positive watts)
  - Lower bound: effective import / load limit (shown as negative watts)

Limits are derived from DERControls across all DERPrograms (resolved by primacy),
with default controls as fallback. Transitions are rendered as linear ramps using
rampTms, setGradW, or AS4777 wGra as appropriate.

Receipt timing:
  - Subscribed programs: device receives controls at created_time (notifications assumed delivered).
  - Polled programs: device receives the control at the first observed GET to the DERControl
    list endpoint for that program after the control's created_time. Falls back to
    the control's start_time if no poll is found.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from cactus_schema.runner.schema import RequestEntry
from envoy.server.model.archive.doe import (
    ArchiveDynamicOperatingEnvelope,
    ArchiveSiteControlGroupDefault,
)
from envoy.server.model.doe import (
    DynamicOperatingEnvelope,
    SiteControlGroup,
    SiteControlGroupDefault,
)
from envoy.server.model.site import SiteDERSetting
from envoy.server.model.subscription import Subscription, SubscriptionResource
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.envoy_common import (
    get_active_site,
    get_site_control_group_defaults_with_archive,
    get_site_controls_active_archived,
)

logger = logging.getLogger(__name__)

_CANBERRA_TZ = ZoneInfo("Australia/Sydney")

# AS4777.2 wGra: 16.67%/min = 0.2778%/s = 27.78 hundredths-of-%-per-sec
_AS4777_WGRA_HUNDREDTHS: float = 16.67 / 60.0 * 100.0

# AS4777 soft-start: fixed 15-second ramp
_AS4777_SOFT_RAMP_SECONDS: float = 15.0

# Grace period after opModConnect:true before returning to normal control
_OP_MOD_CONNECT_GRACE_SECONDS: float = 60.0

# Matches /edev/{n}/derp/{group_id}/derc (with optional query string)
_DERC_PATH_RE = re.compile(r"/edev/\d+/derp/(\d+)/derc")

# Type alias for default control models (active or archived)
_DefaultLike = SiteControlGroupDefault | ArchiveSiteControlGroupDefault

# Cycling colour palette for step-name bands (semi-transparent fills)
_STEP_PALETTE = [
    "rgba(130,179,255,0.45)",
    "rgba(130,220,170,0.45)",
    "rgba(255,210,120,0.45)",
    "rgba(255,140,170,0.45)",
    "rgba(200,140,255,0.45)",
    "rgba(120,220,220,0.45)",
]


# ─── Internal data types ──────────────────────────────────────────────────────


@dataclass
class _EnrichedControl:
    """DERControl with receipt time and effective window pre-computed."""

    doe: DynamicOperatingEnvelope | ArchiveDynamicOperatingEnvelope
    site_control_group_id: int
    primacy: int
    receipt_time: datetime
    effective_start: datetime  # max(doe.start_time, receipt_time)
    effective_end: datetime

    @property
    def export_limit(self) -> float | None:
        v = self.doe.export_limit_watts
        return float(v) if v is not None else None

    @property
    def gen_limit(self) -> float | None:
        v = self.doe.generation_limit_active_watts
        return float(v) if v is not None else None

    @property
    def import_limit(self) -> float | None:
        v = self.doe.import_limit_active_watts
        return float(v) if v is not None else None

    @property
    def load_limit(self) -> float | None:
        v = self.doe.load_limit_active_watts
        return float(v) if v is not None else None

    @property
    def set_connected(self) -> bool | None:
        return self.doe.set_connected

    @property
    def ramp_time_seconds(self) -> float | None:
        v = self.doe.ramp_time_seconds
        return float(v) if v is not None else None


@dataclass
class _LimitEvent:
    """A moment where the target power limit changes."""

    time: datetime
    target: float  # Watts (positive = export ceiling, negative = import floor)
    source: object | None  # _EnrichedControl | _DefaultLike | None (unconstrained)


@dataclass
class _ReceiptMarker:
    """Records when the device received a DERControl."""

    time: datetime
    group_id: int
    is_subscribed: bool  # True = notification delivered; False = polled


# ─── DB queries ───────────────────────────────────────────────────────────────


async def _get_der_setting(session: AsyncSession) -> SiteDERSetting | None:
    site = await get_active_site(session, include_der_settings=True)
    if site is None:
        return None
    for der in site.site_ders:
        if der.site_der_setting is not None:
            return der.site_der_setting
    return None


async def _get_control_groups(session: AsyncSession) -> list[SiteControlGroup]:
    result = await session.execute(select(SiteControlGroup))
    return list(result.scalars().all())


async def _get_subscribed_group_ids(session: AsyncSession) -> set[int]:
    """Returns site_control_group_ids for which an active DERControl subscription exists.
    Only subscriptions with an explicit resource_id are considered; 0 subscriptions is valid
    (device relies entirely on polling)."""
    result = await session.execute(
        select(Subscription).where(
            Subscription.resource_type == SubscriptionResource.DYNAMIC_OPERATING_ENVELOPE,
            Subscription.resource_id.is_not(None),
        )
    )
    return {sub.resource_id for sub in result.scalars().all()}


# ─── Step-interval and receipt-marker helpers ─────────────────────────────────


def _compute_step_intervals(
    request_history: list[RequestEntry],
    test_start: datetime,
    test_end: datetime,
) -> list[tuple[str, datetime, datetime]]:
    """Return (step_name, start, end) intervals derived from sequential step_name changes.

    Each step is active from its first request until the first request of the next step.
    Blank step names are skipped. Steps may repeat."""
    sorted_reqs = sorted(request_history, key=lambda r: r.timestamp)
    intervals: list[tuple[str, datetime, datetime]] = []
    current: str | None = None
    step_start: datetime = test_start

    for req in sorted_reqs:
        name = (req.step_name or "").strip()
        if not name:
            continue
        if name != current:
            if current is not None:
                intervals.append((current, step_start, req.timestamp))
            current = name
            step_start = req.timestamp

    if current is not None:
        intervals.append((current, step_start, test_end))

    return intervals


def _build_receipt_markers(
    enriched: list[_EnrichedControl],
    subscribed_group_ids: set[int],
) -> list[_ReceiptMarker]:
    """One marker per unique (group, receipt_time) pair in enriched controls."""
    seen: set[tuple[int, datetime]] = set()
    markers: list[_ReceiptMarker] = []
    for ctrl in enriched:
        key = (ctrl.site_control_group_id, ctrl.receipt_time)
        if key not in seen:
            seen.add(key)
            markers.append(
                _ReceiptMarker(
                    time=ctrl.receipt_time,
                    group_id=ctrl.site_control_group_id,
                    is_subscribed=ctrl.site_control_group_id in subscribed_group_ids,
                )
            )
    return sorted(markers, key=lambda m: m.time)


# ─── Receipt time computation ─────────────────────────────────────────────────


def _compute_receipt_time(
    doe: DynamicOperatingEnvelope | ArchiveDynamicOperatingEnvelope,
    group_id: int,
    subscribed_group_ids: set[int],
    request_history: list[RequestEntry],
) -> datetime:
    """Returns when the device received this control.

    For subscribed groups: created_time (notifications assumed delivered).
    For polled groups: first GET to /edev/*/derp/{group_id}/derc after created_time.
    Fallback: doe.start_time (if no poll found in history).
    """
    if group_id in subscribed_group_ids:
        return doe.created_time

    for req in request_history:
        if req.method != "GET":
            continue
        if req.timestamp <= doe.created_time:
            continue
        m = _DERC_PATH_RE.search(req.path)
        if m and int(m.group(1)) == group_id:
            return req.timestamp

    logger.debug(
        "power_limit_chart: no DERControl poll found for group %d after %r; " "falling back to start_time",
        group_id,
        doe.created_time,
    )
    return doe.start_time


# ─── Control enrichment ───────────────────────────────────────────────────────


def _effective_end_for_doe(
    doe: DynamicOperatingEnvelope | ArchiveDynamicOperatingEnvelope,
) -> datetime | None:
    """Returns the effective end time for a DOE, or None if no valid window exists."""
    base_end = doe.start_time + timedelta(seconds=doe.duration_seconds)
    if isinstance(doe, ArchiveDynamicOperatingEnvelope):
        if doe.deleted_time is not None and doe.deleted_time > doe.start_time:
            return min(base_end, doe.deleted_time)
        if doe.archive_time is not None and doe.archive_time > doe.start_time:
            return min(base_end, doe.archive_time)
        return None
    return base_end


def _build_enriched_controls(
    all_does: list[DynamicOperatingEnvelope | ArchiveDynamicOperatingEnvelope],
    test_start: datetime,
    groups_by_id: dict[int, SiteControlGroup],
    subscribed_group_ids: set[int],
    request_history: list[RequestEntry],
) -> list[_EnrichedControl]:
    enriched: list[_EnrichedControl] = []
    for doe in all_does:
        # Skip superseded non-archive controls (their effective history is in archive)
        if not isinstance(doe, ArchiveDynamicOperatingEnvelope) and doe.superseded:
            continue

        # Only include controls created during the test
        if doe.created_time < test_start:
            continue

        effective_end = _effective_end_for_doe(doe)
        if effective_end is None:
            continue

        group = groups_by_id.get(doe.site_control_group_id)
        if group is None:
            continue

        receipt = _compute_receipt_time(doe, doe.site_control_group_id, subscribed_group_ids, request_history)
        effective_start = max(doe.start_time, receipt)

        if effective_start >= effective_end:
            continue

        enriched.append(
            _EnrichedControl(
                doe=doe,
                site_control_group_id=doe.site_control_group_id,
                primacy=group.primacy,
                receipt_time=receipt,
                effective_start=effective_start,
                effective_end=effective_end,
            )
        )
    return enriched


# ─── Default control helpers ──────────────────────────────────────────────────


def _default_active_window(d: _DefaultLike) -> tuple[datetime, datetime] | None:
    """Returns the (start, end) time window during which this default was active.
    Returns None if the archive_time is missing (should not happen; silently ignored)."""
    if isinstance(d, ArchiveSiteControlGroupDefault):
        if d.archive_time is None:
            return None
        return d.changed_time, d.archive_time
    # Active default: valid from changed_time to the far future
    far_future = datetime(9999, 1, 1, tzinfo=d.changed_time.tzinfo)
    return d.changed_time, far_future


def _build_defaults_by_group(all_defaults: list[_DefaultLike]) -> dict[int, list[_DefaultLike]]:
    result: dict[int, list[_DefaultLike]] = {}
    for d in all_defaults:
        result.setdefault(d.site_control_group_id, []).append(d)
    return result


# ─── Disconnect / opModConnect tracking ───────────────────────────────────────


def _compute_disconnect_intervals(
    enriched: list[_EnrichedControl],
    test_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Returns intervals during which the device active power is forced to 0,
    including the 1-minute grace period after reconnect.

    Reconnection is triggered by either:
    - An opModConnect=True active control (at its effective_start), or
    - The opModConnect=False control expiring (at its effective_end).
    """
    # False controls contribute two events: disconnect at effective_start, reconnect at effective_end.
    # True controls contribute one event: reconnect at effective_start.
    connected_events: list[tuple[datetime, bool]] = []
    for ctrl in enriched:
        if ctrl.set_connected is False:
            connected_events.append((ctrl.effective_start, False))
            connected_events.append((ctrl.effective_end, True))  # expiry = reconnect
        elif ctrl.set_connected is True:
            connected_events.append((ctrl.effective_start, True))
    connected_events.sort(key=lambda e: e[0])

    disconnect_intervals: list[tuple[datetime, datetime]] = []
    i = 0
    while i < len(connected_events):
        t, connected = connected_events[i]
        if not connected:
            disconnect_start = t
            reconnect_time: datetime | None = None
            for j in range(i + 1, len(connected_events)):
                if connected_events[j][1]:
                    reconnect_time = connected_events[j][0]
                    break
            if reconnect_time is None:
                disconnect_end = test_end
            else:
                disconnect_end = min(reconnect_time + timedelta(seconds=_OP_MOD_CONNECT_GRACE_SECONDS), test_end)
            disconnect_intervals.append((disconnect_start, disconnect_end))
        i += 1

    return disconnect_intervals


# ─── Effective limit computation ──────────────────────────────────────────────


def _find_active_control_at(
    T: datetime,
    group_id: int,
    enriched: list[_EnrichedControl],
) -> _EnrichedControl | None:
    """Find the highest-priority active enriched control for the given group at time T."""
    best: _EnrichedControl | None = None
    for ctrl in enriched:
        if ctrl.site_control_group_id != group_id:
            continue
        if not (ctrl.effective_start <= T < ctrl.effective_end):
            continue
        if best is None:
            best = ctrl
        # Archive records take priority over active (they represent confirmed historical windows)
        elif isinstance(ctrl.doe, ArchiveDynamicOperatingEnvelope) and not isinstance(
            best.doe, ArchiveDynamicOperatingEnvelope
        ):
            best = ctrl
    return best


def _find_active_default_at(
    T: datetime,
    group_id: int,
    defaults_by_group: dict[int, list[_DefaultLike]],
) -> _DefaultLike | None:
    """Find the active default control for the given group at time T."""
    for d in defaults_by_group.get(group_id, []):
        window = _default_active_window(d)
        if window is None:
            continue
        start, end = window
        if start <= T < end:
            return d
    return None


def _min_notnone(*vals: Optional[float]) -> float | None:
    valid = [v for v in vals if v is not None]
    return min(valid) if valid else None


def _get_effective_upper_at(
    T: datetime,
    sorted_groups: list[SiteControlGroup],
    enriched: list[_EnrichedControl],
    defaults_by_group: dict[int, list[_DefaultLike]],
) -> tuple[float | None, object | None]:
    """Returns (effective_limit_watts, source) for the upper bound (export/gen) at time T.
    Returns (None, None) if unconstrained."""
    for group in sorted_groups:
        gid = group.site_control_group_id

        ctrl = _find_active_control_at(T, gid, enriched)
        if ctrl is not None:
            limit = _min_notnone(ctrl.export_limit, ctrl.gen_limit)
            if limit is not None:
                return limit, ctrl
            # Active control for this group but no export/gen limit set → skip to next group
            continue

        default = _find_active_default_at(T, gid, defaults_by_group)
        if default is not None:
            exp = default.export_limit_active_watts
            gen = default.generation_limit_active_watts
            limit = _min_notnone(
                float(exp) if exp is not None else None,
                float(gen) if gen is not None else None,
            )
            if limit is not None:
                return limit, default

    return None, None


def _get_effective_lower_at(
    T: datetime,
    sorted_groups: list[SiteControlGroup],
    enriched: list[_EnrichedControl],
    defaults_by_group: dict[int, list[_DefaultLike]],
) -> tuple[float | None, object | None]:
    """Returns (effective_limit_watts, source) for the lower bound (import/load) at time T.
    Returns a positive magnitude value - caller negates it for display."""
    for group in sorted_groups:
        gid = group.site_control_group_id

        ctrl = _find_active_control_at(T, gid, enriched)
        if ctrl is not None:
            limit = _min_notnone(ctrl.import_limit, ctrl.load_limit)
            if limit is not None:
                return limit, ctrl
            continue

        default = _find_active_default_at(T, gid, defaults_by_group)
        if default is not None:
            imp = default.import_limit_active_watts
            load = default.load_limit_active_watts
            limit = _min_notnone(
                float(imp) if imp is not None else None,
                float(load) if load is not None else None,
            )
            if limit is not None:
                return limit, default

    return None, None


# ─── Ramp duration computation ────────────────────────────────────────────────


def _rate_based_duration(grad_w_hundredths: float, delta_w: float, set_max_w: float) -> float:
    """Compute ramp duration from a grad_w rate (hundredths of %/s of setMaxW)."""
    rate_w_per_sec = (grad_w_hundredths / 10000.0) * set_max_w
    if rate_w_per_sec <= 0.0:
        return 0.0
    return delta_w / rate_w_per_sec


def _compute_ramp_seconds(
    source: object | None,
    der_grad_w: int,
    delta_w: float,
    set_max_w: float,
) -> float:
    """Compute ramp duration in seconds for a transition to source.

    Rules:
      To DER control:    rampTms → setGradW (DERSettings) → 15s fixed
      To default:        DefaultDERControl.setGradW → setGradW (DERSettings) → AS4777 wGra rate
      To unconstrained:  setGradW (DERSettings) → AS4777 wGra rate
    """
    if isinstance(source, _EnrichedControl):
        ramp_t = source.ramp_time_seconds
        if ramp_t is not None and ramp_t > 0.0:
            return ramp_t
        if der_grad_w > 0:
            return _rate_based_duration(float(der_grad_w), delta_w, set_max_w)
        return _AS4777_SOFT_RAMP_SECONDS

    if source is not None:
        default_grad = getattr(source, "ramp_rate_percent_per_second", None)
        if default_grad is not None and default_grad > 0:
            return _rate_based_duration(float(default_grad), delta_w, set_max_w)
        if der_grad_w > 0:
            return _rate_based_duration(float(der_grad_w), delta_w, set_max_w)
        return _rate_based_duration(_AS4777_WGRA_HUNDREDTHS, delta_w, set_max_w)

    # Unconstrained
    if der_grad_w > 0:
        return _rate_based_duration(float(der_grad_w), delta_w, set_max_w)
    return _rate_based_duration(_AS4777_WGRA_HUNDREDTHS, delta_w, set_max_w)


def _ramp_description(source: object | None, der_grad_w: int, ramp_secs: float) -> str:
    """Human-readable string describing which ramp rule was applied."""
    dur = f"{ramp_secs:.0f}s"
    if isinstance(source, _EnrichedControl):
        if source.ramp_time_seconds and source.ramp_time_seconds > 0:
            return f"rampTms={source.ramp_time_seconds:.0f}s"
        if der_grad_w > 0:
            return f"setGradW={der_grad_w} ({dur})"
        return "AS4777 soft-start (15s)"
    if source is not None:
        default_grad = getattr(source, "ramp_rate_percent_per_second", None)
        if default_grad and default_grad > 0:
            return f"Default setGradW={default_grad} ({dur})"
        if der_grad_w > 0:
            return f"setGradW={der_grad_w} ({dur})"
        return f"AS4777 wGra ({dur})"
    if der_grad_w > 0:
        return f"setGradW={der_grad_w} ({dur})"
    return f"AS4777 wGra ({dur})"


# ─── Event collection ─────────────────────────────────────────────────────────


def _collect_event_times(
    enriched: list[_EnrichedControl],
    defaults_by_group: dict[int, list[_DefaultLike]],
    disconnect_intervals: list[tuple[datetime, datetime]],
    test_start: datetime,
    test_end: datetime,
) -> list[datetime]:
    """Collect all times at which the effective limit might change."""
    times: set[datetime] = {test_start, test_end}
    for ctrl in enriched:
        times.add(ctrl.effective_start)
        times.add(ctrl.effective_end)
    for defaults in defaults_by_group.values():
        for d in defaults:
            window = _default_active_window(d)
            if window is None:
                continue
            start, end = window
            if test_start < start < test_end:
                times.add(start)
            if test_start < end < test_end:
                times.add(end)
    for ds, de in disconnect_intervals:
        if test_start <= ds <= test_end:
            times.add(ds)
        if test_start <= de <= test_end:
            times.add(de)
    return sorted(t for t in times if test_start <= t <= test_end)


def _build_limit_events(
    is_upper: bool,
    event_times: list[datetime],
    sorted_groups: list[SiteControlGroup],
    enriched: list[_EnrichedControl],
    defaults_by_group: dict[int, list[_DefaultLike]],
    disconnect_intervals: list[tuple[datetime, datetime]],
    set_max_w: float,
) -> list[_LimitEvent]:
    """Build list of limit target changes over the test timeline."""
    events: list[_LimitEvent] = []
    prev_target: float | None = None

    for T in event_times:
        is_disconnected = any(ds <= T < de for ds, de in disconnect_intervals)

        if is_disconnected:
            target = 0.0
            source = None
        elif is_upper:
            raw, source = _get_effective_upper_at(T, sorted_groups, enriched, defaults_by_group)
            target = raw if raw is not None else set_max_w
        else:
            raw, source = _get_effective_lower_at(T, sorted_groups, enriched, defaults_by_group)
            target = -(raw if raw is not None else set_max_w)

        if prev_target is None or abs(target - prev_target) > 0.1:
            events.append(_LimitEvent(time=T, target=target, source=source))
            prev_target = target

    return events


# ─── Trace building ───────────────────────────────────────────────────────────


def _build_trace(
    limit_events: list[_LimitEvent],
    test_start: datetime,
    test_end: datetime,
    initial_value: float,
    der_grad_w: int,
    set_max_w: float,
) -> list[tuple[datetime, float, str]]:
    """Convert limit events into a piecewise-linear trace with ramps.

    Returns a list of (time, watts, hover_text) tuples. hover_text is non-empty only
    at ramp-start points and describes the ramp rule applied (e.g. 'rampTms=120s').
    """
    points: list[tuple[datetime, float, str]] = [(test_start, initial_value, "")]

    # Track the current ramp: from (ramp_start_t, ramp_start_v) to (ramp_end_t, ramp_end_v)
    ramp_start_t = test_start
    ramp_start_v = initial_value
    ramp_end_t = test_start
    ramp_end_v = initial_value

    for ev in limit_events:
        if ev.time <= test_start:
            continue
        if ev.time > test_end:
            break

        # Determine the current value at ev.time (accounting for any active ramp)
        ramp_duration_secs = (ramp_end_t - ramp_start_t).total_seconds()
        if ramp_duration_secs <= 0.0 or ev.time >= ramp_end_t:
            current_v = ramp_end_v
        else:
            frac = (ev.time - ramp_start_t).total_seconds() / ramp_duration_secs
            frac = max(0.0, min(1.0, frac))
            current_v = ramp_start_v + frac * (ramp_end_v - ramp_start_v)

        if abs(ev.target - current_v) < 0.1:
            continue

        delta_w = abs(ev.target - current_v)
        ramp_secs = _compute_ramp_seconds(ev.source, der_grad_w, delta_w, set_max_w)
        desc = _ramp_description(ev.source, der_grad_w, ramp_secs)
        hover = f"<br>→ {ev.target:.0f} W  ({desc})"

        points.append((ev.time, current_v, hover))

        ramp_end_dt = ev.time + timedelta(seconds=ramp_secs)
        if ramp_end_dt <= test_end:
            points.append((ramp_end_dt, ev.target, ""))
        else:
            # Ramp extends past test end - clip and interpolate
            if ramp_secs > 0.0:
                frac = (test_end - ev.time).total_seconds() / ramp_secs
                frac = max(0.0, min(1.0, frac))
                v_at_end = current_v + frac * (ev.target - current_v)
            else:
                v_at_end = ev.target
            points.append((test_end, v_at_end, ""))

        ramp_start_t = ev.time
        ramp_start_v = current_v
        ramp_end_t = ramp_end_dt
        ramp_end_v = ev.target

    # Ensure the trace reaches test_end
    if points[-1][0] < test_end:
        ramp_duration_secs = (ramp_end_t - ramp_start_t).total_seconds()
        if ramp_duration_secs <= 0.0 or test_end >= ramp_end_t:
            v_final = ramp_end_v
        else:
            frac = (test_end - ramp_start_t).total_seconds() / ramp_duration_secs
            v_final = ramp_start_v + frac * (ramp_end_v - ramp_start_v)
        points.append((test_end, v_final, ""))

    return points


# ─── Chart rendering ──────────────────────────────────────────────────────────


def _duration_label(seconds: float) -> str:
    """Convert a seconds offset to a compact label: '30s', '5m', '1h2m'."""
    s = int(abs(seconds))
    if s < 60:
        return f"{s}s"
    mins = s // 60
    secs = s % 60
    if mins < 60:
        return f"{mins}m" if secs == 0 else f"{mins}m{secs}s"
    hours = mins // 60
    rem = mins % 60
    return f"{hours}h{rem}m" if rem else f"{hours}h"


def _choose_tick_interval_seconds(duration_secs: float) -> int:
    """Pick a sensible tick interval so there are roughly 8-20 ticks."""
    for interval in [60, 120, 300, 600, 900, 1800, 3600, 7200]:
        if duration_secs / interval <= 20:
            return interval
    return 3600


def _render_html_chart(
    upper_trace: list[tuple[datetime, float, str]],
    lower_trace: list[tuple[datetime, float, str]],
    test_start: datetime,
    test_end: datetime,
    set_max_w: float,
    step_intervals: list[tuple[str, datetime, datetime]],
    receipt_markers: list[_ReceiptMarker],
) -> str:
    duration_secs = (test_end - test_start).total_seconds()
    tick_interval = _choose_tick_interval_seconds(duration_secs)

    tick_vals: list[float] = []
    bottom_labels: list[str] = []  # Relative time + UTC (shown below x-axis)
    top_labels: list[str] = []  # AEDT/AEST (shown above chart)

    t_secs = 0.0
    while t_secs <= duration_secs + 1:
        T = test_start + timedelta(seconds=t_secs)
        tick_vals.append(t_secs)
        bottom_labels.append(f"{_duration_label(t_secs)}<br>{T.strftime('%H:%M')} UTC")
        top_labels.append(T.astimezone(_CANBERRA_TZ).strftime("%H:%M %Z"))
        t_secs += tick_interval

    def to_rel(t: datetime) -> float:
        return (t - test_start).total_seconds()

    y_max = set_max_w * 1.1
    y_min = -set_max_w * 1.1
    has_steps = bool(step_intervals)
    bottom_margin = 175 if has_steps else 105

    fig = go.Figure()

    # ── Main limit traces ────────────────────────────────────────────────────
    fig.add_trace(
        go.Scatter(
            x=[to_rel(t) for t, _, _ in upper_trace],
            y=[v for _, v, _ in upper_trace],
            mode="lines",
            name="Upper limit (Export / Gen)",
            line=dict(color="#e74c3c", width=2),
            customdata=[[h] for _, _, h in upper_trace],
            hovertemplate="%{y:.0f} W%{customdata[0]}<extra>Upper</extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[to_rel(t) for t, _, _ in lower_trace],
            y=[v for _, v, _ in lower_trace],
            mode="lines",
            name="Lower limit (Import / Load)",
            line=dict(color="#3498db", width=2),
            customdata=[[h] for _, _, h in lower_trace],
            hovertemplate="%{y:.0f} W%{customdata[0]}<extra>Lower</extra>",
        )
    )

    # ── Reference lines ──────────────────────────────────────────────────────
    fig.add_hline(y=0, line_color="black", line_width=1, line_dash="dash", opacity=0.4)
    fig.add_hline(
        y=set_max_w,
        line_color="grey",
        line_width=1,
        line_dash="dot",
        annotation_text=f"setMaxW ({int(set_max_w)} W)",
        annotation_position="top right",
    )
    fig.add_hline(
        y=-set_max_w,
        line_color="grey",
        line_width=1,
        line_dash="dot",
        annotation_text=f"−setMaxW (−{int(set_max_w)} W)",
        annotation_position="bottom right",
    )

    # ── Invisible secondary x-axis trace for AEDT tick labels ────────────────
    fig.add_trace(
        go.Scatter(
            x=tick_vals,
            y=[None] * len(tick_vals),
            xaxis="x2",
            showlegend=False,
            hoverinfo="skip",
        )
    )

    # ── Control receipt markers ──────────────────────────────────────────────
    # Vertical lines (shapes) — one per unique receipt event
    for m in receipt_markers:
        color = "#27ae60" if m.is_subscribed else "#e67e22"
        fig.add_shape(
            type="line",
            xref="x", yref="paper",
            x0=to_rel(m.time), x1=to_rel(m.time),
            y0=0.02, y1=0.98,
            line=dict(color=color, width=1.5, dash="dot"),
            opacity=0.55,
        )

    # Hover markers for receipt times (triangle markers + legend dummy traces)
    has_subs = any(m.is_subscribed for m in receipt_markers)
    has_polls = any(not m.is_subscribed for m in receipt_markers)
    if has_subs:
        fig.add_trace(
            go.Scatter(
                x=[None], y=[None],
                mode="lines",
                line=dict(color="#27ae60", width=1.5, dash="dot"),
                name="Notif receipt",
            )
        )
    if has_polls:
        fig.add_trace(
            go.Scatter(
                x=[None], y=[None],
                mode="lines",
                line=dict(color="#e67e22", width=1.5, dash="dot"),
                name="Poll receipt",
            )
        )
    if receipt_markers:
        fig.add_trace(
            go.Scatter(
                x=[to_rel(m.time) for m in receipt_markers],
                y=[set_max_w * 0.55] * len(receipt_markers),
                mode="markers",
                marker=dict(
                    symbol="triangle-down",
                    size=9,
                    color=["#27ae60" if m.is_subscribed else "#e67e22" for m in receipt_markers],
                    opacity=0.85,
                ),
                customdata=[["Notification" if m.is_subscribed else "Poll", m.group_id] for m in receipt_markers],
                hovertemplate="%{customdata[0]} receipt — Group %{customdata[1]}<extra>Receipt</extra>",
                showlegend=False,
            )
        )

    # ── Step name strips at the bottom (paper coordinates) ───────────────────
    if has_steps:
        # "Steps" axis label, left of the strip
        fig.add_annotation(
            xref="paper", yref="paper",
            x=-0.04, y=-0.27,
            text="<b>Steps</b>",
            showarrow=False,
            font=dict(size=9, color="#555"),
            xanchor="right",
            yanchor="middle",
        )
        for i, (name, start, end) in enumerate(step_intervals):
            color = _STEP_PALETTE[i % len(_STEP_PALETTE)]
            x0 = to_rel(max(start, test_start))
            x1 = to_rel(min(end, test_end))
            if x1 <= x0:
                continue
            fig.add_shape(
                type="rect",
                xref="x", yref="paper",
                x0=x0, x1=x1,
                y0=-0.36, y1=-0.18,
                fillcolor=color,
                line=dict(color="rgba(0,0,0,0.18)", width=0.5),
                layer="below",
            )
            interval_secs = x1 - x0
            if interval_secs >= 20:
                label = name if len(name) <= 20 else name[:18] + "…"
                fig.add_annotation(
                    xref="x", yref="paper",
                    x=(x0 + x1) / 2,
                    y=-0.27,
                    text=label,
                    showarrow=False,
                    font=dict(size=8),
                    xanchor="center",
                    yanchor="middle",
                )

    # ── Layout ───────────────────────────────────────────────────────────────
    fig.update_layout(
        title=dict(text="Expected Device Power Limits", font=dict(size=16)),
        height=650,
        xaxis=dict(
            title="",
            tickmode="array",
            tickvals=tick_vals,
            ticktext=bottom_labels,
            range=[0, duration_secs],
            showgrid=True,
            gridcolor="rgba(0,0,0,0.08)",
        ),
        xaxis2=dict(
            overlaying="x",
            side="top",
            tickmode="array",
            tickvals=tick_vals,
            ticktext=top_labels,
            title="Canberra (AEDT / AEST)",
            matches="x",
            range=[0, duration_secs],
            showgrid=False,
        ),
        yaxis=dict(
            title="Active Power (W)",
            range=[y_min, y_max],
            showgrid=True,
            gridcolor="rgba(0,0,0,0.08)",
            zeroline=False,
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="right", x=1),
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(t=120, b=bottom_margin, l=80, r=120),
        hovermode="x unified",
        # ── Export / Import view toggle ──────────────────────────────────────
        updatemenus=[
            dict(
                type="buttons",
                direction="left",
                x=0.0,
                y=-0.08 if not has_steps else -0.12,
                xanchor="left",
                yanchor="top",
                buttons=[
                    dict(
                        label="Export view",
                        method="relayout",
                        args=[{"yaxis.range": [y_min, y_max], "yaxis.autorange": False}],
                    ),
                    dict(
                        label="Import view",
                        method="relayout",
                        args=[{"yaxis.range": [y_max, y_min], "yaxis.autorange": False}],
                    ),
                ],
                font=dict(size=11),
                bgcolor="white",
                bordercolor="#bbb",
                borderwidth=1,
            )
        ],
    )

    return fig.to_html(full_html=True, include_plotlyjs=True)


# ─── Public entry point ───────────────────────────────────────────────────────


async def generate_power_limit_chart_html(
    session: AsyncSession,
    test_start: datetime,
    test_end: datetime,
    request_history: list[RequestEntry],
) -> str | None:
    """Generate a standalone HTML chart of expected device power limits.

    Returns None if the chart cannot be generated (e.g. DERSettings never sent,
    setMaxW is zero, or no SiteControlGroups exist).
    """
    der_setting = await _get_der_setting(session)
    if der_setting is None:
        logger.warning("power_limit_chart: no SiteDERSetting found - skipping chart")
        return None

    set_max_w = float(der_setting.max_w_value) * (10.0**der_setting.max_w_multiplier)
    if set_max_w <= 0.0:
        logger.warning("power_limit_chart: setMaxW <= 0 - skipping chart")
        return None

    control_groups = await _get_control_groups(session)
    if not control_groups:
        logger.warning("power_limit_chart: no SiteControlGroups found - skipping chart")
        return None
    groups_by_id = {g.site_control_group_id: g for g in control_groups}
    sorted_groups = sorted(control_groups, key=lambda g: g.primacy)

    all_does = await get_site_controls_active_archived(session)
    all_defaults = await get_site_control_group_defaults_with_archive(session)
    defaults_by_group = _build_defaults_by_group(all_defaults)

    subscribed_group_ids = await _get_subscribed_group_ids(session)

    enriched = _build_enriched_controls(all_does, test_start, groups_by_id, subscribed_group_ids, request_history)

    disconnect_intervals = _compute_disconnect_intervals(enriched, test_end)

    event_times = _collect_event_times(enriched, defaults_by_group, disconnect_intervals, test_start, test_end)

    der_grad_w = der_setting.grad_w

    upper_events = _build_limit_events(
        True, event_times, sorted_groups, enriched, defaults_by_group, disconnect_intervals, set_max_w
    )
    lower_events = _build_limit_events(
        False, event_times, sorted_groups, enriched, defaults_by_group, disconnect_intervals, set_max_w
    )

    upper_trace = _build_trace(upper_events, test_start, test_end, set_max_w, der_grad_w, set_max_w)
    lower_trace = _build_trace(lower_events, test_start, test_end, -set_max_w, der_grad_w, set_max_w)

    step_intervals = _compute_step_intervals(request_history, test_start, test_end)
    receipt_markers = _build_receipt_markers(enriched, subscribed_group_ids)

    return _render_html_chart(
        upper_trace, lower_trace, test_start, test_end, set_max_w, step_intervals, receipt_markers
    )
