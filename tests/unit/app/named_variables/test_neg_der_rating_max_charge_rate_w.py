import pytest
from assertical.fake.generator import generate_class_instance
from envoy.server.model.site import Site, SiteDERRating

from cactus_runner.app.database import begin_session
from cactus_runner.plugin.backends.envoy.resolver import EnvoyResolver


@pytest.mark.asyncio
async def test_resolve_named_variable_neg_der_rating_max_charge_rate_w_single_setting(pg_base_config):
    """If there is a single DERSetting in the db  - return it"""
    max_charge_rate_w_value = 12345
    max_charge_rate_w_multiplier = -2
    async with begin_session() as session:
        session.add(
            generate_class_instance(
                Site,
                nmi_checksum=None,
                site_id=None,
                aggregator_id=1,
                site_der_rating=generate_class_instance(
                    SiteDERRating,
                    site_der_rating_id=None,
                    site_id=None,
                    max_charge_rate_w_value=max_charge_rate_w_value,
                    max_charge_rate_w_multiplier=max_charge_rate_w_multiplier,
                ),
            )
        )
        await session.commit()

    async with begin_session() as session:
        resolver = EnvoyResolver(lambda: session)
        result = await resolver.resolve_named_variable_neg_der_rating_max_charge_rate_w()
        assert isinstance(result, float)
        assert result == -123.45
