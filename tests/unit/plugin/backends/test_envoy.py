from datetime import UTC, datetime

import pytest
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from envoy.server.model import DynamicOperatingEnvelope, Site, SiteControlGroup
from envoy.server.model.archive import ArchiveDynamicOperatingEnvelope

from cactus_runner.plugin.backends import EnvoyBackend


@pytest.mark.anyio
async def test_get_site_controls(pg_base_config, envoy_admin_client) -> None:
    """Tests that the get_site_controls returns all controls as expected."""
    async with generate_async_session(pg_base_config) as session:
        site_control_group = generate_class_instance(SiteControlGroup, seed=101)
        site1 = generate_class_instance(Site, seed=202, site_id=1, aggregator_id=1)
        session.add(site1)

        for idx, control_id in enumerate(range(1, 6)):
            control = generate_class_instance(
                DynamicOperatingEnvelope,
                seed=idx,
                site=site1,
                site_control_group=site_control_group,
                calculation_log_id=None,
                dynamic_operating_envelope_id=control_id,
            )
            session.add(control)

        for idx, control_id in enumerate(range(6, 11)):
            control = generate_class_instance(
                ArchiveDynamicOperatingEnvelope,
                seed=idx * 1001,
                site_id=site1.site_id,
                deleted_time=datetime(2022, 11, 14, tzinfo=UTC),
                site_control_group_id=site_control_group.site_control_group_id,
                calculation_log_id=None,
                dynamic_operating_envelope_id=control_id,
            )
            session.add(control)

        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session=session, admin_client=envoy_admin_client)

        controls = await backend.get_site_controls()

        assert [int(c.site_control_id) for c in controls] == list(range(1, 11))
        assert [int(c.site_control_id) for c in controls if c.deleted_time is not None] == list(range(6, 11))
        assert [int(c.site_control_id) for c in controls if c.deleted_time is None] == list(range(1, 6))
