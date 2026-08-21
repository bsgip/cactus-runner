from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from envoy.server.model import (
    DynamicOperatingEnvelope,
    Site,
    SiteControlGroup,
    SiteDERRating,
    SiteDERSetting,
    SiteDERStatus,
    SiteGroup,
)
from envoy.server.model.archive import ArchiveDynamicOperatingEnvelope
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.plugin.backends.envoy import EnvoyAdminClient, EnvoyBackend
from cactus_runner.plugin.backends.envoy.resolver import EnvoyResolver


@pytest.mark.anyio
async def test_get_site_controls(pg_base_config, envoy_admin_client) -> None:
    """Tests that the get_site_controls returns all controls as expected."""
    async with generate_async_session(pg_base_config) as session:
        site_group = generate_class_instance(SiteGroup, seed=202)
        site_control_group = generate_class_instance(
            SiteControlGroup,
            seed=101,
            required_site_group_id=None,
        )
        session.add(site_group)
        session.add(site_control_group)

        for idx, control_id in enumerate(range(1, 6)):
            control = generate_class_instance(
                DynamicOperatingEnvelope,
                seed=idx,
                site_group_id=site_group.site_group_id,
                site_control_group=site_control_group,
                calculation_log_id=None,
                dynamic_operating_envelope_id=control_id,
            )
            session.add(control)

        for idx, control_id in enumerate(range(6, 11)):
            control = generate_class_instance(
                ArchiveDynamicOperatingEnvelope,
                seed=idx * 1001,
                site_group_id=site_group.site_group_id,
                deleted_time=datetime(2022, 11, 14, tzinfo=UTC),
                site_control_group_id=site_control_group.site_control_group_id,
                calculation_log_id=None,
                dynamic_operating_envelope_id=control_id,
            )
            session.add(control)

        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=envoy_admin_client)

        controls = await backend.get_site_controls()

        assert [int(c.site_control_id) for c in controls] == list(range(1, 11))
        assert [int(c.site_control_id) for c in controls if c.deleted_time is not None] == list(range(6, 11))
        assert [int(c.site_control_id) for c in controls if c.deleted_time is None] == list(range(1, 6))


@pytest.mark.anyio
async def test_get_end_device_metadata(mocker) -> None:
    """Test that EndDeviceMetadata is correctly populated from active site.

    This test was largely part of a test_status.py unit test but it is mainly rooted in envoy as the
    backend, so it was migrated for the most part here to protect the behaviour of the existing
    metadata creation logic for it.
    """
    # Arrange
    mock_session = AsyncMock(spec=AsyncSession)
    mock_envoy_client = Mock(spec=EnvoyAdminClient)
    backend = EnvoyBackend(session_factory=lambda: mock_session, admin_client=mock_envoy_client)
    mock_resolver = Mock(spec=EnvoyResolver)
    mocker.patch.object(backend, "get_expression_resolver", return_value=mock_resolver)
    mock_resolver.resolve_named_variable_der_setting_max_w.return_value = 5000

    mock_get_active_site = mocker.patch("cactus_runner.plugin.backends.envoy.backend._get_active_site_with_der")

    # Build model instances with specific overrides for fields we assert on
    site_der_setting = generate_class_instance(
        SiteDERSetting,
        seed=401,
        doe_modes_enabled=7,  # DOESupportedMode: EXPORT_LIMIT_W | IMPORT_LIMIT_W | GENERATION_LIMIT_W
        modes_enabled=None,
        max_w_value=5000,
        max_w_multiplier=0,
        grad_w=100,
    )
    site_der_rating = generate_class_instance(
        SiteDERRating,
        seed=501,
        der_type=4,  # DERType.PHOTOVOLTAIC_SYSTEM
        modes_supported=None,
        max_w_value=6000,
        max_w_multiplier=0,
    )
    site_der_status = generate_class_instance(
        SiteDERStatus,
        seed=601,
        inverter_status=2,  # InverterStatusType.SLEEPING
        alarm_status=None,
    )
    site = generate_class_instance(Site, seed=101, aggregator_id=1, site_id=42)
    site.site_der_setting = site_der_setting
    site.site_der_rating = site_der_rating
    site.site_der_status = site_der_status
    mock_get_active_site.return_value = site

    # Act
    metadata = await backend.get_end_device_metadata()

    # Assert - EndDeviceMetadata
    assert metadata is not None
    assert metadata.edevid == 42
    assert metadata.lfdi == site.lfdi
    assert metadata.sfdi == site.sfdi
    assert metadata.nmi == site.nmi
    assert metadata.aggregator_id == 1
    assert metadata.set_max_w == 5000
    assert metadata.doe_modes_enabled == 7
    assert metadata.device_category == site.device_category
    assert metadata.timezone_id == site.timezone_id

    # DERSettings
    assert metadata.der_settings is not None
    assert metadata.der_settings.max_w == 5000
    assert metadata.der_settings.grad_w == 100
    assert metadata.der_settings.modes_enabled is None
    assert metadata.der_settings.doe_modes_enabled == [
        "OP_MOD_EXPORT_LIMIT_W",
        "OP_MOD_IMPORT_LIMIT_W",
        "OP_MOD_GENERATION_LIMIT_W",
    ]

    # DERCapability
    assert metadata.der_capability is not None
    assert metadata.der_capability.der_type == "PHOTOVOLTAIC_SYSTEM"
    assert metadata.der_capability.max_w == 6000
    assert metadata.der_capability.modes_supported is None

    # DERStatus
    assert metadata.der_status is not None
    assert metadata.der_status.inverter_status == "SLEEPING"
    assert metadata.der_status.alarm_status is None
