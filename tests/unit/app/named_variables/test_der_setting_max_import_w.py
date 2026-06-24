import pytest
from assertical.fake.generator import generate_class_instance
from cactus_test_definitions.errors import UnresolvableVariableError
from envoy.server.model.site import Site, SiteDERSetting

from cactus_runner.app import resolvers
from cactus_runner.app.database import begin_session


def _add_der_setting(session, **der_setting_kwargs) -> None:
    session.add(
        generate_class_instance(
            Site,
            site_id=None,
            aggregator_id=1,
            site_der_setting=generate_class_instance(
                SiteDERSetting,
                site_der_setting_id=None,
                site_der_id=None,
                **der_setting_kwargs,
            ),
        )
    )


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_max_import_w_empty(pg_empty_config):
    """If there is nothing in the DB - fail in a predictable way"""
    async with begin_session() as session:
        with pytest.raises(UnresolvableVariableError, match="DERSetting"):
            await resolvers.resolve_named_variable_der_setting_max_import_w(session)


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_max_import_w_uses_charge_rate(pg_base_config):
    """When setMaxChargeRateW is set - it is preferred over setMaxW"""
    async with begin_session() as session:
        _add_der_setting(
            session,
            max_charge_rate_w_value=12345,
            max_charge_rate_w_multiplier=-2,
            max_w_value=999,
            max_w_multiplier=0,
        )
        await session.commit()

    async with begin_session() as session:
        result = await resolvers.resolve_named_variable_der_setting_max_import_w(session)
        assert isinstance(result, float)
        assert result == 123.45


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_max_import_w_falls_back_to_max_w(pg_base_config):
    """When setMaxChargeRateW is absent - fall back to the mandatory setMaxW"""
    async with begin_session() as session:
        _add_der_setting(
            session,
            max_charge_rate_w_value=None,
            max_charge_rate_w_multiplier=None,
            max_w_value=678,
            max_w_multiplier=1,
        )
        await session.commit()

    async with begin_session() as session:
        result = await resolvers.resolve_named_variable_der_setting_max_import_w(session)
        assert isinstance(result, float)
        assert result == 6780
