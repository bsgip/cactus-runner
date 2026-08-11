import pytest
from assertical.fake.generator import generate_class_instance
from cactus_test_definitions.errors import UnresolvableVariableError
from envoy.server.model.site import Site, SiteDERSetting

from cactus_runner.app.database import begin_session
from cactus_runner.plugin.backends.envoy.resolver import EnvoyResolver


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_max_va_empty(pg_empty_config):
    """If there is nothing in the DB - fail in a predictable way"""
    async with begin_session() as session:
        with pytest.raises(UnresolvableVariableError, match="DERSetting"):
            resolver = EnvoyResolver(session)
            await resolver.resolve_named_variable_der_setting_max_va()


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_max_va_no_setting(pg_base_config):
    """If there is everything up to (but not including) a DERSetting in the db  - fail in a predictable way"""
    async with begin_session() as session:
        session.add(generate_class_instance(Site, site_id=None, aggregator_id=1))
        await session.commit()

    async with begin_session() as session:
        with pytest.raises(UnresolvableVariableError, match="setMaxVA"):
            resolver = EnvoyResolver(session)
            await resolver.resolve_named_variable_der_setting_max_va()


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_max_va_single_setting(pg_base_config):
    """If there is a single DERSetting in the db  - return it"""
    max_va_value = 12345
    max_va_multiplier = -2
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
                    max_va_value=max_va_value,
                    max_va_multiplier=max_va_multiplier,
                ),
            )
        )
        await session.commit()

    async with begin_session() as session:
        resolver = EnvoyResolver(session)
        result = await resolver.resolve_named_variable_der_setting_max_va()
        assert isinstance(result, float)
        assert result == 123.45


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_max_va_many_settings(pg_base_config):
    """If there are multiple DERSettings - return the most recent DERSetting"""
    max_va_value = 123
    max_va_multiplier = 2
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
                    max_va_value=max_va_value,
                    max_va_multiplier=max_va_multiplier,
                ),
            )
        )
        await session.commit()

    async with begin_session() as session:
        resolver = EnvoyResolver(session)
        result = await resolver.resolve_named_variable_der_setting_max_va()
        assert isinstance(result, float)
        assert result == 12300
