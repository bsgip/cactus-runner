import pytest
from assertical.fake.generator import generate_class_instance
from cactus_test_definitions.errors import UnresolvableVariableError
from envoy.server.model.site import Site, SiteDER, SiteDERRating

from cactus_runner.app import resolvers
from cactus_runner.app.database import begin_session


@pytest.mark.asyncio
async def test_resolve_named_variable_der_rating_max_va_empty(pg_empty_config):
    """If there is nothing in the DB - fail in a predictable way"""
    async with begin_session() as session:
        with pytest.raises(UnresolvableVariableError, match="DERCapability"):
            await resolvers.resolve_named_variable_der_rating_max_va(session)


@pytest.mark.asyncio
async def test_resolve_named_variable_der_rating_max_va_no_setting(pg_base_config):
    """If there is everything up to (but not including) a DERSetting in the db  - fail in a predictable way"""
    async with begin_session() as session:
        session.add(
            generate_class_instance(
                Site, site_id=None, aggregator_id=1, site_ders=[generate_class_instance(SiteDER, site_id=None)]
            )
        )
        await session.commit()

    async with begin_session() as session:
        with pytest.raises(UnresolvableVariableError, match="rtgMaxVA"):
            await resolvers.resolve_named_variable_der_rating_max_va(session)


@pytest.mark.asyncio
async def test_resolve_named_variable_der_rating_max_va_single_setting(pg_base_config):
    """If there is a single DERSetting in the db  - return it"""
    max_va_value = 12345
    max_va_multiplier = -2
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
                        site_der_rating=generate_class_instance(
                            SiteDERRating,
                            site_der_rating_id=None,
                            site_der_id=None,
                            max_va_value=max_va_value,
                            max_va_multiplier=max_va_multiplier,
                        ),
                    )
                ],
            )
        )
        await session.commit()

    async with begin_session() as session:
        result = await resolvers.resolve_named_variable_der_rating_max_va(session)
        assert isinstance(result, float)
        assert result == 123.45


@pytest.mark.asyncio
async def test_resolve_named_variable_der_rating_max_va_many_settings(pg_base_config):
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
                site_ders=[
                    generate_class_instance(
                        SiteDER,
                        seed=2102,
                        site_id=None,
                        site_der_rating=generate_class_instance(
                            SiteDERRating,
                            seed=2202,
                            site_der_rating_id=None,
                            site_der_id=None,
                        ),
                    )
                ],
            )
        )

        # This site's SiteDERRating should be returned as it's change_time will be the most recent
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
                        site_der_rating=generate_class_instance(
                            SiteDERRating,
                            seed=3203,
                            site_der_rating_id=None,
                            site_der_id=None,
                            max_va_value=max_va_value,
                            max_va_multiplier=max_va_multiplier,
                        ),
                    )
                ],
            )
        )
        await session.commit()

    async with begin_session() as session:
        result = await resolvers.resolve_named_variable_der_rating_max_va(session)
        assert isinstance(result, float)
        assert result == 12300
