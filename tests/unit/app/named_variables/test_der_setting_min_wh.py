import pytest
from assertical.fake.generator import generate_class_instance
from cactus_test_definitions.errors import UnresolvableVariableError
from envoy.server.model.site import Site, SiteDERSetting

from cactus_runner.app import resolvers
from cactus_runner.app.database import begin_session


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_min_wh_empty(pg_empty_config):
    """If there is nothing in the DB - fail in a predictable way"""
    async with begin_session() as session:
        with pytest.raises(UnresolvableVariableError, match="DERSetting"):
            await resolvers.resolve_named_variable_der_setting_min_wh(session)


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_min_wh_no_setting(pg_base_config):
    """If there is everything up to (but not including) a DERSetting in the db  - fail in a predictable way"""
    async with begin_session() as session:
        session.add(generate_class_instance(Site, optional_is_none=True, site_id=None, aggregator_id=1))
        await session.commit()

    async with begin_session() as session:
        with pytest.raises(UnresolvableVariableError, match="setMinWh"):
            await resolvers.resolve_named_variable_der_setting_min_wh(session)


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_min_wh_single_setting(pg_base_config):
    """If there is a single DERSetting in the db  - return it"""
    min_wh_value = 12345
    min_wh_multiplier = -2
    async with begin_session() as session:
        session.add(
            generate_class_instance(
                Site,
                site_id=None,
                aggregator_id=1,
                site_der_setting=generate_class_instance(
                    SiteDERSetting,
                    site_der_setting_id=None,
                    site_id=None,
                    min_wh_value=min_wh_value,
                    min_wh_multiplier=min_wh_multiplier,
                ),
            )
        )
        await session.commit()

    async with begin_session() as session:
        result = await resolvers.resolve_named_variable_der_setting_min_wh(session)
        assert isinstance(result, float)
        assert result == 123.45


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_min_wh_many_settings(pg_base_config):
    """If there are multiple DERSettings - return the most recent DERSetting"""
    min_wh_value = 123
    min_wh_multiplier = 2
    async with begin_session() as session:
        session.add(
            generate_class_instance(
                Site,
                seed=1001,
                site_id=None,
                aggregator_id=1,
            )
        )

        session.add(
            generate_class_instance(
                Site,
                seed=2002,
                site_id=None,
                aggregator_id=1,
                site_der_setting=generate_class_instance(
                    SiteDERSetting,
                    seed=2202,
                    site_der_setting_id=None,
                    site_id=None,
                ),
            )
        )

        # This site's SiteDERSetting should be returned as it's change_time will be the most recent
        session.add(
            generate_class_instance(
                Site,
                seed=3003,
                site_id=None,
                aggregator_id=1,
                site_der_setting=generate_class_instance(
                    SiteDERSetting,
                    seed=3203,
                    site_der_setting_id=None,
                    site_id=None,
                    min_wh_value=min_wh_value,
                    min_wh_multiplier=min_wh_multiplier,
                ),
            )
        )
        await session.commit()

    async with begin_session() as session:
        result = await resolvers.resolve_named_variable_der_setting_min_wh(session)
        assert isinstance(result, float)
        assert result == 12300
