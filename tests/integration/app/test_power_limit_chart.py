"""
Integration tests for power_limit_chart.generate_power_limit_chart_html.

Each test builds a realistic DB scenario, generates the HTML chart, and writes it
to /tmp/cactus_charts/ so it can be opened in a browser for visual inspection.

Run with:
    pytest tests/integration/app/test_power_limit_chart.py -v -s
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http import HTTPStatus
from pathlib import Path

import pytest
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from cactus_schema.runner.schema import HTTPMethod, RequestEntry
from envoy.server.model.doe import DynamicOperatingEnvelope, SiteControlGroup, SiteControlGroupDefault
from envoy.server.model.site import Site, SiteDER, SiteDERSetting

from cactus_runner.app.power_limit_chart import generate_power_limit_chart_html

OUTPUT_DIR = Path("/tmp/cactus_charts")

T0 = datetime(2026, 2, 26, 9, 30, 0, tzinfo=timezone.utc)  # Test start time


def _out(name: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / name


def _poll(group_id: int, when: datetime, req_id: int = 0) -> RequestEntry:
    """Construct a fake DERControl list poll request."""
    return RequestEntry(
        url=f"https://envoy.example.com/edev/1/derp/{group_id}/derc",
        path=f"/edev/1/derp/{group_id}/derc",
        method=HTTPMethod.GET,
        status=HTTPStatus.OK,
        timestamp=when,
        step_name="",
        body_xml_errors=[],
        request_id=req_id,
    )


def _make_doe(
    site: Site,
    group: SiteControlGroup,
    offset_minutes: float,
    duration_minutes: float,
    *,
    export_limit: Decimal | None = None,
    import_limit: Decimal | None = None,
    gen_limit: Decimal | None = None,
    load_limit: Decimal | None = None,
    ramp_time_seconds: Decimal | None = None,
    set_connected: bool | None = None,
    seed: int = 1,
) -> DynamicOperatingEnvelope:
    start = T0 + timedelta(minutes=offset_minutes)
    duration = int(duration_minutes * 60)
    end = start + timedelta(seconds=duration)
    return generate_class_instance(
        DynamicOperatingEnvelope,
        seed=seed,
        site=site,
        site_control_group=group,
        calculation_log_id=None,
        start_time=start,
        end_time=end,
        duration_seconds=duration,
        created_time=start - timedelta(seconds=30),
        changed_time=start - timedelta(seconds=30),
        export_limit_watts=export_limit,
        import_limit_active_watts=import_limit,
        generation_limit_active_watts=gen_limit,
        load_limit_active_watts=load_limit,
        ramp_time_seconds=ramp_time_seconds,
        set_connected=set_connected,
        superseded=False,
        set_energized=None,
        set_point_percentage=None,
        randomize_start_seconds=None,
    )


def _make_der_site(aggregator_id: int, max_w: int = 10000, grad_w: int = 28) -> Site:
    """Build a Site with one SiteDER and SiteDERSetting."""
    der_setting = generate_class_instance(
        SiteDERSetting,
        site_der_setting_id=None,
        site_der_id=None,
        max_w_value=max_w,
        max_w_multiplier=0,
        grad_w=grad_w,
        soft_grad_w=None,
    )
    der = generate_class_instance(SiteDER, seed=1, site_id=None, site_der_setting=der_setting)
    site = generate_class_instance(Site, seed=1, site_id=1, aggregator_id=aggregator_id)
    site.site_ders = [der]
    return site


# ─── Scenario A: Single program, export curtailment steps with AS4777 ramps ──


@pytest.mark.anyio
async def test_chart_single_program_export_curtailment(pg_base_config):
    """
    One DERProgram (primacy 1). Export is stepped down and back up over 40 minutes.
    Device polls 60 seconds after each control is created. AS4777 ramp rate (grad_w=28).

    Expected visual:
      - Upper trace starts at setMaxW (10000W)
      - Ramps down to 5000W at T+5m (visible ramp over ~3 min at AS4777 rate)
      - Ramps down to 0W at T+15m
      - Ramps back up to 10000W at T+25m
      - Lower trace flat at -10000W (no import limit set)
    """
    test_end = T0 + timedelta(minutes=45)

    created_times: list[datetime] = []

    async with generate_async_session(pg_base_config) as session:
        site = _make_der_site(aggregator_id=1)
        session.add(site)
        group = generate_class_instance(SiteControlGroup, seed=1, site_control_group_id=1, primacy=1)
        session.add(group)

        ctrls = [
            _make_doe(site, group, offset_minutes=5,  duration_minutes=10, export_limit=Decimal("5000"),  seed=10),
            _make_doe(site, group, offset_minutes=15, duration_minutes=10, export_limit=Decimal("0"),     seed=20),
            _make_doe(site, group, offset_minutes=25, duration_minutes=15, export_limit=Decimal("10000"), seed=30),
        ]
        session.add_all(ctrls)
        await session.flush()  # Assign IDs without closing session
        created_times = [c.created_time for c in ctrls]  # Read while session still open
        await session.commit()

    polls = [_poll(1, t + timedelta(seconds=60), req_id=i) for i, t in enumerate(created_times)]

    async with generate_async_session(pg_base_config) as session:
        html = await generate_power_limit_chart_html(session, T0, test_end, polls)

    assert html is not None, "Chart generation returned None"
    out = _out("scenario_A_single_program_export.html")
    out.write_text(html)
    print(f"\n  ✓ Scenario A → {out}")


# ─── Scenario B: Two programs, primacy resolution, import + export limits ─────


@pytest.mark.anyio
async def test_chart_multi_program_primacy(pg_base_config):
    """
    Two DERPrograms operating simultaneously on different limit types:
      - Program 1 (primacy 1): sets IMPORT limits (lower trace)
      - Program 2 (primacy 2): sets EXPORT limits (upper trace)

    Expected visual:
      - Upper trace driven by Program 2 controls, stepped and ramped
      - Lower trace driven by Program 1 controls, independently ramped
    """
    test_end = T0 + timedelta(minutes=50)
    created_times: dict[str, datetime] = {}

    async with generate_async_session(pg_base_config) as session:
        site = _make_der_site(aggregator_id=1)
        session.add(site)
        grp1 = generate_class_instance(SiteControlGroup, seed=1, site_control_group_id=1, primacy=1)
        grp2 = generate_class_instance(SiteControlGroup, seed=2, site_control_group_id=2, primacy=2)
        session.add_all([grp1, grp2])

        ctrls = {
            "imp1": _make_doe(site, grp1, offset_minutes=5,  duration_minutes=15, import_limit=Decimal("0"),    seed=11),
            "imp2": _make_doe(site, grp1, offset_minutes=20, duration_minutes=15, import_limit=Decimal("3000"), seed=12),
            "exp1": _make_doe(site, grp2, offset_minutes=10, duration_minutes=10, export_limit=Decimal("4000"), seed=21),
            "exp2": _make_doe(site, grp2, offset_minutes=30, duration_minutes=15, export_limit=Decimal("2000"), seed=22),
        }
        session.add_all(ctrls.values())
        await session.flush()
        created_times = {k: v.created_time for k, v in ctrls.items()}
        await session.commit()

    polls = [
        _poll(1, created_times["imp1"] + timedelta(seconds=90), req_id=1),
        _poll(1, created_times["imp2"] + timedelta(seconds=90), req_id=2),
        _poll(2, created_times["exp1"] + timedelta(seconds=90), req_id=3),
        _poll(2, created_times["exp2"] + timedelta(seconds=90), req_id=4),
    ]

    async with generate_async_session(pg_base_config) as session:
        html = await generate_power_limit_chart_html(session, T0, test_end, polls)

    assert html is not None
    out = _out("scenario_B_multi_program_primacy.html")
    out.write_text(html)
    print(f"\n  ✓ Scenario B → {out}")


# ─── Scenario C: rampTms on controls, default control as baseline ─────────────


@pytest.mark.anyio
async def test_chart_ramptms_and_defaults(pg_base_config):
    """
    Demonstrates rampTms (explicit 120s ramp) and a default control baseline.

    Program 1 (primacy 1):
      - Default export=8000W active from test start
      - T+5m: export=1000W with rampTms=120s → visible 2-minute ramp
      - T+15m: control expires → ramps back to default 8000W via grad_w rate
      - T+25m: export=0W with no rampTms → falls to grad_w (AS4777) rate

    Expected visual:
      - Upper trace starts at 8000W (default)
      - 2-minute sloped ramp to 1000W at T+5m
      - grad_w ramp back to 8000W at T+15m
      - grad_w ramp down to 0W at T+25m
    """
    test_end = T0 + timedelta(minutes=40)
    created_times: dict[str, datetime] = {}

    async with generate_async_session(pg_base_config) as session:
        site = _make_der_site(aggregator_id=1)
        session.add(site)
        grp1 = generate_class_instance(SiteControlGroup, seed=1, site_control_group_id=1, primacy=1)
        session.add(grp1)

        session.add(generate_class_instance(
            SiteControlGroupDefault,
            seed=1,
            site_control_group=grp1,
            import_limit_active_watts=None,
            export_limit_active_watts=Decimal("8000"),
            generation_limit_active_watts=None,
            load_limit_active_watts=None,
            ramp_rate_percent_per_second=None,
            changed_time=T0 - timedelta(minutes=1),
        ))

        ctrls = {
            "ctrl1": _make_doe(site, grp1, offset_minutes=5,  duration_minutes=10,
                               export_limit=Decimal("1000"), ramp_time_seconds=Decimal("120"), seed=10),
            "ctrl2": _make_doe(site, grp1, offset_minutes=25, duration_minutes=10,
                               export_limit=Decimal("0"), seed=20),
        }
        session.add_all(ctrls.values())
        await session.flush()
        created_times = {k: v.created_time for k, v in ctrls.items()}
        await session.commit()

    # Near-instant polls (simulating subscription-speed receipt)
    polls = [
        _poll(1, created_times["ctrl1"] + timedelta(seconds=1), req_id=1),
        _poll(1, created_times["ctrl2"] + timedelta(seconds=1), req_id=2),
    ]

    async with generate_async_session(pg_base_config) as session:
        html = await generate_power_limit_chart_html(session, T0, test_end, polls)

    assert html is not None
    out = _out("scenario_C_ramptms_and_defaults.html")
    out.write_text(html)
    print(f"\n  ✓ Scenario C → {out}")


# ─── Scenario D: opModConnect disconnect and reconnect grace period ───────────


@pytest.mark.anyio
async def test_chart_op_mod_connect(pg_base_config):
    """
    Demonstrates opModConnect=False disconnect and 1-minute grace after reconnect.

    Program 1:
      - Default export=7000W
      - T+5m: export control 5000W begins (spanning full test)
      - T+10m: opModConnect=False → device reverts to default (7000W)
      - T+20m: opModConnect=True → 1-minute grace period (still on default)
      - T+21m: grace ends → resumes 5000W export control

    Expected visual:
      - Upper trace: 5000W from T+5m
      - Jump to 7000W (default) at T+10m
      - Stay at 7000W until T+21m
      - Drop back to 5000W at T+21m
    """
    test_end = T0 + timedelta(minutes=35)
    created_times: dict[str, datetime] = {}

    async with generate_async_session(pg_base_config) as session:
        site = _make_der_site(aggregator_id=1)
        session.add(site)
        grp1 = generate_class_instance(SiteControlGroup, seed=1, site_control_group_id=1, primacy=1)
        session.add(grp1)

        session.add(generate_class_instance(
            SiteControlGroupDefault,
            seed=1,
            site_control_group=grp1,
            import_limit_active_watts=None,
            export_limit_active_watts=Decimal("7000"),
            generation_limit_active_watts=None,
            load_limit_active_watts=None,
            ramp_rate_percent_per_second=None,
            changed_time=T0 - timedelta(minutes=1),
        ))

        ctrls = {
            "export":     _make_doe(site, grp1, offset_minutes=5,  duration_minutes=25,
                                    export_limit=Decimal("5000"), seed=10),
            "disconnect": _make_doe(site, grp1, offset_minutes=10, duration_minutes=5,
                                    set_connected=False, seed=20),
            "reconnect":  _make_doe(site, grp1, offset_minutes=20, duration_minutes=5,
                                    set_connected=True,  seed=30),
        }
        session.add_all(ctrls.values())
        await session.flush()
        created_times = {k: v.created_time for k, v in ctrls.items()}
        await session.commit()

    polls = [
        _poll(1, created_times["export"]     + timedelta(seconds=60), req_id=1),
        _poll(1, created_times["disconnect"] + timedelta(seconds=60), req_id=2),
        _poll(1, created_times["reconnect"]  + timedelta(seconds=60), req_id=3),
    ]

    async with generate_async_session(pg_base_config) as session:
        html = await generate_power_limit_chart_html(session, T0, test_end, polls)

    assert html is not None
    out = _out("scenario_D_op_mod_connect.html")
    out.write_text(html)
    print(f"\n  ✓ Scenario D → {out}")


# ─── Scenario E: GEN-10-like — two programs, late polls, defaults ─────────────


@pytest.mark.anyio
async def test_chart_gen10_like(pg_base_config):
    """
    Approximates the GEN-10 test structure with two programs and 5-minute polling.

      - Program 1 (primacy 1): import curtailment controls (utility)
      - Program 2 (primacy 2): export limit controls (operator)
      - Both have default controls
      - Device polls every 5 minutes per program

    Expected visual:
      - Upper trace: export limits from program 2, possibly with visible late-poll delay
      - Lower trace: import limits from program 1
      - Controls take effect visibly after the poll, not at their start_time
    """
    test_end = T0 + timedelta(minutes=60)
    created_times: dict[str, datetime] = {}

    async with generate_async_session(pg_base_config) as session:
        site = _make_der_site(aggregator_id=1)
        session.add(site)
        grp1 = generate_class_instance(SiteControlGroup, seed=1, site_control_group_id=1, primacy=1)
        grp2 = generate_class_instance(SiteControlGroup, seed=2, site_control_group_id=2, primacy=2)
        session.add_all([grp1, grp2])

        session.add(generate_class_instance(
            SiteControlGroupDefault, seed=1, site_control_group=grp1,
            import_limit_active_watts=Decimal("0"), export_limit_active_watts=None,
            generation_limit_active_watts=None, load_limit_active_watts=None,
            ramp_rate_percent_per_second=None, changed_time=T0 - timedelta(minutes=2),
        ))
        session.add(generate_class_instance(
            SiteControlGroupDefault, seed=2, site_control_group=grp2,
            import_limit_active_watts=None, export_limit_active_watts=Decimal("10000"),
            generation_limit_active_watts=None, load_limit_active_watts=None,
            ramp_rate_percent_per_second=None, changed_time=T0 - timedelta(minutes=2),
        ))

        ctrls = {
            "imp1": _make_doe(site, grp1, offset_minutes=5,  duration_minutes=14, import_limit=Decimal("5000"), seed=11),
            "imp2": _make_doe(site, grp1, offset_minutes=20, duration_minutes=14, import_limit=Decimal("0"),    seed=12),
            "imp3": _make_doe(site, grp1, offset_minutes=35, duration_minutes=20, import_limit=Decimal("3000"), seed=13),
            "exp1": _make_doe(site, grp2, offset_minutes=8,  duration_minutes=12, export_limit=Decimal("7000"), seed=21),
            "exp2": _make_doe(site, grp2, offset_minutes=22, duration_minutes=13, export_limit=Decimal("3000"), seed=22),
            "exp3": _make_doe(site, grp2, offset_minutes=38, duration_minutes=17, export_limit=Decimal("6000"), seed=23),
        }
        session.add_all(ctrls.values())
        await session.flush()
        created_times = {k: v.created_time for k, v in ctrls.items()}
        await session.commit()

    # 5-minute polling interval
    polls = [
        _poll(1, created_times["imp1"] + timedelta(minutes=5), req_id=1),
        _poll(1, created_times["imp2"] + timedelta(minutes=5), req_id=2),
        _poll(1, created_times["imp3"] + timedelta(minutes=5), req_id=3),
        _poll(2, created_times["exp1"] + timedelta(minutes=5), req_id=10),
        _poll(2, created_times["exp2"] + timedelta(minutes=5), req_id=11),
        _poll(2, created_times["exp3"] + timedelta(minutes=5), req_id=12),
    ]

    async with generate_async_session(pg_base_config) as session:
        html = await generate_power_limit_chart_html(session, T0, test_end, polls)

    assert html is not None
    out = _out("scenario_E_gen10_like.html")
    out.write_text(html)
    print(f"\n  ✓ Scenario E → {out}")
    print(f"\n  Open all charts: ls {OUTPUT_DIR}/")
