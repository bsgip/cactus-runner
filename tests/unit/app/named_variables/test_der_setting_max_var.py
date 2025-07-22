import pytest
from assertical.fake.generator import generate_class_instance
from cactus_test_definitions.errors import UnresolvableVariableError
from envoy.server.model.site import Site, SiteDER, SiteDERSetting

from cactus_runner.app import resolvers
from cactus_runner.app.database import begin_session


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_max_var_empty(pg_empty_config):
    """If there is nothing in the DB - fail in a predictable way"""
    async with begin_session() as session:
        with pytest.raises(UnresolvableVariableError, match="DERSetting"):
            await resolvers.resolve_named_variable_der_setting_max_var(session)


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_max_var_no_setting(pg_base_config):
    """If there is everything up to (but not including) a DERSetting in the db  - fail in a predictable way"""
    async with begin_session() as session:
        session.add(
            generate_class_instance(
                Site, site_id=None, aggregator_id=1, site_ders=[generate_class_instance(SiteDER, site_id=None)]
            )
        )
        await session.commit()

    async with begin_session() as session:
        with pytest.raises(UnresolvableVariableError, match="setMaxVar"):
            await resolvers.resolve_named_variable_der_setting_max_var(session)


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_max_var_single_setting(pg_base_config):
    """If there is a single DERSetting in the db  - return it"""
    max_var_value = 12345
    max_var_multiplier = -2
    async with begin_session() as session:
        session.add(
            generate_class_instance(
                Site,
                site_id=None,
                aggregator_id=1,
                site_ders=[
                    generate_class_instance(
                        SiteDER,
                        site_id=None,
                        site_der_setting=generate_class_instance(
                            SiteDERSetting,
                            site_der_setting_id=None,
                            site_der_id=None,
                            max_var_value=max_var_value,
                            max_var_multiplier=max_var_multiplier,
                        ),
                    )
                ],
            )
        )
        await session.commit()

    async with begin_session() as session:
        result = await resolvers.resolve_named_variable_der_setting_max_var(session)
        assert isinstance(result, float)
        assert result == 123.45


@pytest.mark.asyncio
async def test_resolve_named_variable_der_setting_max_var_many_settings(pg_base_config):
    """If there are multiple DERSettings - return the most recent DERSetting"""
    max_var_value = 123
    max_var_multiplier = 2
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
                site_ders=[
                    generate_class_instance(
                        SiteDER,
                        seed=2102,
                        site_id=None,
                        site_der_setting=generate_class_instance(
                            SiteDERSetting,
                            seed=2202,
                            site_der_setting_id=None,
                            site_der_id=None,
                        ),
                    )
                ],
            )
        )

        # This site's SiteDERSetting should be returned as it's change_time will be the most recent
        session.add(
            generate_class_instance(
                Site,
                seed=3003,
                site_id=None,
                aggregator_id=1,
                site_ders=[
                    generate_class_instance(
                        SiteDER,
                        seed=3103,
                        site_id=None,
                        site_der_setting=generate_class_instance(
                            SiteDERSetting,
                            seed=3203,
                            site_der_setting_id=None,
                            site_der_id=None,
                            max_var_value=max_var_value,
                            max_var_multiplier=max_var_multiplier,
                        ),
                    )
                ],
            )
        )
        await session.commit()

    async with begin_session() as session:
        result = await resolvers.resolve_named_variable_der_setting_max_var(session)
        assert isinstance(result, float)
        assert result == 12300
