import pytest
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from envoy.server.model.aggregator import Aggregator
from envoy.server.model.doe import DynamicOperatingEnvelope, SiteControlGroup
from envoy.server.model.site import Site
from sqlalchemy import func, select

from cactus_runner.app import precondition


@pytest.mark.anyio
async def test_reset_db_on_content(pg_base_config):
    """Tests that reset_db works if there is content to reset"""

    # Insert some extra content
    async with generate_async_session(pg_base_config) as session:
        s1 = generate_class_instance(Site, seed=1, site_id=None, aggregator_id=0)
        s2 = generate_class_instance(Site, seed=2, site_id=None, aggregator_id=1)

        sc1 = generate_class_instance(SiteControlGroup, seed=3, site_control_group_id=None)

        doe1 = generate_class_instance(
            DynamicOperatingEnvelope,
            seed=4,
            dynamic_operating_envelope_id=None,
            site_control_group_id=None,
            site_id=None,
            calculation_log_id=None,
        )
        doe2 = generate_class_instance(
            DynamicOperatingEnvelope,
            seed=5,
            dynamic_operating_envelope_id=None,
            site_control_group_id=None,
            site_id=None,
            calculation_log_id=None,
        )

        doe1.site = s1
        doe1.site_control_group = sc1
        doe2.site = s1
        doe2.site_control_group = sc1

        session.add(doe1)
        session.add(doe2)
        session.add(sc1)
        session.add(s1)
        session.add(s2)
        await session.commit()

    # Act
    await precondition.reset_db()

    # Assert that the DB is empty and that the sequences are reset to the beginning
    async with generate_async_session(pg_base_config) as session:
        # Counts should all be 0
        assert (await session.execute(select(func.count()).select_from(Site))).scalar_one() == 0
        assert (await session.execute(select(func.count()).select_from(Aggregator))).scalar_one() == 0
        assert (await session.execute(select(func.count()).select_from(SiteControlGroup))).scalar_one() == 0
        assert (await session.execute(select(func.count()).select_from(DynamicOperatingEnvelope))).scalar_one() == 0

        # Inserts should also be inserting from 1
        agg = generate_class_instance(Aggregator, seed=1, aggregator_id=None)
        session.add(agg)
        await session.flush()

        assert agg.aggregator_id == 1, "Sequence should've been reset"

        s1 = generate_class_instance(Site, seed=2, site_id=None, aggregator_id=1)
        session.add(s1)
        await session.flush()
        assert s1.site_id == 1, "Sequence should've been reset"

        sc1 = generate_class_instance(SiteControlGroup, seed=3, site_control_group_id=None)
        session.add(sc1)
        await session.flush()
        assert sc1.site_control_group_id == 1, "Sequence should've been reset"
