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


def _poll(group_id: int, when: datetime, req_id: int = 0, step_name: str = "") -> RequestEntry:
    """Construct a fake DERControl list poll request."""
    return RequestEntry(
        url=f"https://envoy.example.com/edev/1/derp/{group_id}/derc",
        path=f"/edev/1/derp/{group_id}/derc",
        method=HTTPMethod.GET,
        status=HTTPStatus.OK,
        timestamp=when,
        step_name=step_name,
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
    set_energized: bool | None = None,
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
        set_energized=set_energized,
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
    Demonstrates opModConnect=False disconnect (power=0) and 1-minute grace after explicit
    True-control reconnect.

    Program 1:
      - Default export=7000W
      - T+5m: export control 5000W begins (spanning full test)
      - T+10m: opModConnect=False (duration 5min, expires T+15m) → power forced to 0
      - T+15m: False control expires → reconnect triggered, 1-min grace (power still 0)
      - T+16m: grace ends → resumes 5000W export control
      - T+20m: opModConnect=True (already reconnected, no effect here)

    Expected visual:
      - Upper trace: 5000W from T+5m, drops to 0 at T+10m, ramps back to 5000W at T+16m
      - Lower trace: flat at -10000W (no import limit set)
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


# ─── Scenario D2: opModConnect — expiry-triggered reconnect, no True control ──


@pytest.mark.anyio
async def test_chart_op_mod_connect_expiry(pg_base_config):
    """
    opModConnect=False control expires with no subsequent True control.
    Reconnection is triggered purely by expiry.

    Program 1:
      - Default export=7000W
      - T+5m: export control 5000W begins (spanning full test)
      - T+10m: opModConnect=False (duration 5min, expires T+15m) → power forced to 0
      - T+15m: False control expires → reconnect triggered, 1-min grace (power still 0)
      - T+16m: grace ends → resumes 5000W export control

    Expected visual:
      - Upper trace: 5000W from T+5m, drops to 0 at T+10m, ramps back to 5000W at T+16m
      - No True control — reconnect is entirely expiry-driven
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
        }
        session.add_all(ctrls.values())
        await session.flush()
        created_times = {k: v.created_time for k, v in ctrls.items()}
        await session.commit()

    polls = [
        _poll(1, created_times["export"]     + timedelta(seconds=60), req_id=1),
        _poll(1, created_times["disconnect"] + timedelta(seconds=60), req_id=2),
    ]

    async with generate_async_session(pg_base_config) as session:
        html = await generate_power_limit_chart_html(session, T0, test_end, polls)

    assert html is not None
    out = _out("scenario_D2_op_mod_connect_expiry.html")
    out.write_text(html)
    print(f"\n  ✓ Scenario D2 → {out}")


# ─── Scenario E: GEN-10 DERC4/5/6 — opModConnect + primacy + supersede ────────


@pytest.mark.anyio
async def test_chart_gen10_derc456(pg_base_config):
    """
    Approximates the GEN-10 DERC4/5/6 phase (primacy validation for generators).

    Two groups mirror GEN-10's FSA1 (primacy 1) and FSA2 (primacy 2):

      DERC4 — group 1, primacy 1, T+0 to T+5m:
        opModConnect=False + genLim=0.  Device is disconnected (power→0) from its
        receipt at T+1m until 1-min after expiry at T+6m.

      DERC5 — group 2, primacy 2, T+0 to T+8m:
        opModExpLimW=200% (20000 W — shown above setMaxW reference line).
        Effective once DERC4 grace ends at T+6m; group 1 has no export default
        so group 2's control wins.

      DERC6 — group 2, primacy 2, T+8m to T+13m:
        opModExpLimW=50% (5000 W).  Received at T+9m (non-overlapping with DERC5,
        no supersede record needed).  Upper trace ramps from 20000→10000→5000 W.

    Device is polled (no subscriptions). grad_w=200 keeps ramps short enough to
    see clearly on a 20-minute chart.

    Expected visual:
      Upper trace:
        T+0→T+1m    unconstrained (10000 W)
        T+1m        DERC4 received → ramp down to 0 W (50 s, AS4777 wGra)
        T+1m→T+6m   0 W (disconnected + grace)
        T+6m        grace ends → ramp up to 20000 W (DERC5, 100 s)
        T+8m        DERC5 expires → ramp to 10000 W (unconstrained, 50 s)
        T+9m        DERC6 received → ramp down to 5000 W (25 s)
        T+13m       DERC6 expires → ramp back to 10000 W (25 s)
      Lower trace: flat at −10000 W (no import controls or defaults)
      Step strips: GET-DERC-4 / GET-DERC-5 / WAIT-OBSERVE-DERC-5 /
                   GET-DERC-6 / WAIT-OBSERVE-DERC-6 / WAIT-OBSERVE-DERP-1-6-DEFAULTS
      Orange receipt markers at T+1m (grp 1), T+1m30s (grp 2), T+9m (grp 2).
    """
    test_end = T0 + timedelta(minutes=20)

    async with generate_async_session(pg_base_config) as session:
        site = _make_der_site(aggregator_id=1, max_w=10000, grad_w=200)
        session.add(site)
        # Group 1 = FSA1 / DERP1, high priority. No export default → group 2 can win after DERC4.
        grp1 = generate_class_instance(SiteControlGroup, seed=1, site_control_group_id=1, primacy=1)
        # Group 2 = FSA2 / DERP6, lower priority, holds the export controls.
        grp2 = generate_class_instance(SiteControlGroup, seed=2, site_control_group_id=2, primacy=2)
        session.add_all([grp1, grp2])

        # DERC4: opModConnect=False + genLim=0 on group 1 (5 min)
        derc4 = _make_doe(site, grp1, offset_minutes=0, duration_minutes=5,
                          gen_limit=Decimal("0"), set_connected=False, seed=40)
        # DERC5: export 200% on group 2 (8 min — ends just before DERC6 starts)
        derc5 = _make_doe(site, grp2, offset_minutes=0, duration_minutes=8,
                          export_limit=Decimal("20000"), seed=50)
        # DERC6: export 50% on group 2 (5 min — non-overlapping with DERC5)
        derc6 = _make_doe(site, grp2, offset_minutes=8, duration_minutes=5,
                          export_limit=Decimal("5000"), seed=60)
        session.add_all([derc4, derc5, derc6])
        await session.flush()
        ct4, ct5, ct6 = derc4.created_time, derc5.created_time, derc6.created_time
        await session.commit()

    polls = [
        # T+1m: device polls /derp/1/derc — DERC4 received (triggers disconnect)
        _poll(1, ct4 + timedelta(minutes=1, seconds=30),  req_id=1, step_name="GET-DERC-4"),
        # T+1m30s: device polls /derp/2/derc — DERC5 received (masked by disconnect until T+6m)
        _poll(2, ct5 + timedelta(minutes=2),              req_id=2, step_name="GET-DERC-5"),
        # T+7m: re-poll during wait step — device should now be following DERC5 (200%)
        _poll(2, T0 + timedelta(minutes=7),               req_id=3, step_name="WAIT-OBSERVE-DERC-5"),
        # T+9m: device polls /derp/2/derc — DERC6 received (DERC5 already expired at T+8m)
        _poll(2, ct6 + timedelta(minutes=1, seconds=30),  req_id=4, step_name="GET-DERC-6"),
        # T+11m: re-poll during wait step — device should be following DERC6 (50%)
        _poll(2, T0 + timedelta(minutes=11),              req_id=5, step_name="WAIT-OBSERVE-DERC-6"),
        # T+15m: poll after DERC6 expires — device returns to unconstrained
        _poll(1, T0 + timedelta(minutes=15),              req_id=6, step_name="WAIT-OBSERVE-DERP-1-6-DEFAULTS"),
    ]

    async with generate_async_session(pg_base_config) as session:
        html = await generate_power_limit_chart_html(session, T0, test_end, polls)

    assert html is not None
    out = _out("scenario_E_gen10_derc456.html")
    out.write_text(html)
    print(f"\n  ✓ Scenario E → {out}")


# ─── Scenario F: opModEnergise — de-energise and re-energise grace period ─────


@pytest.mark.anyio
async def test_chart_op_mod_energise(pg_base_config):
    """
    Demonstrates opModEnergise=False de-energise (power=0) and 1-minute grace after
    explicit True-control re-energise, mirroring the opModConnect behaviour.

    Program 1:
      - Default export=7000W
      - T+5m: export control 5000W begins (spanning full test)
      - T+10m: opModEnergise=False (duration 5min, expires T+15m) → power forced to 0
      - T+15m: False control expires → re-energise triggered, 1-min grace (power still 0)
      - T+16m: grace ends → resumes 5000W export control
      - T+20m: opModEnergise=True (already re-energised, no effect here)

    Expected visual:
      - Upper trace: 5000W from T+5m, drops to 0 at T+10m, ramps back to 5000W at T+16m
      - Lower trace: flat at -10000W (no import limit set)
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
            "export":       _make_doe(site, grp1, offset_minutes=5,  duration_minutes=25,
                                      export_limit=Decimal("5000"), seed=10),
            "de-energise":  _make_doe(site, grp1, offset_minutes=10, duration_minutes=5,
                                      set_energized=False, seed=20),
            "re-energise":  _make_doe(site, grp1, offset_minutes=20, duration_minutes=5,
                                      set_energized=True,  seed=30),
        }
        session.add_all(ctrls.values())
        await session.flush()
        created_times = {k: v.created_time for k, v in ctrls.items()}
        await session.commit()

    polls = [
        _poll(1, created_times["export"]      + timedelta(seconds=60), req_id=1),
        _poll(1, created_times["de-energise"] + timedelta(seconds=60), req_id=2),
        _poll(1, created_times["re-energise"] + timedelta(seconds=60), req_id=3),
    ]

    async with generate_async_session(pg_base_config) as session:
        html = await generate_power_limit_chart_html(session, T0, test_end, polls)

    assert html is not None
    out = _out("scenario_F_op_mod_energise.html")
    out.write_text(html)
    print(f"\n  ✓ Scenario F → {out}")
    print(f"\n  Open all charts: ls {OUTPUT_DIR}/")
