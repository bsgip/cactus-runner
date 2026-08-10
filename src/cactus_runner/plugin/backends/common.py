from collections.abc import Sequence
from datetime import datetime
from operator import attrgetter
from typing import Protocol, runtime_checkable

from cactus_runner.models import ActiveTestProcedure
from cactus_runner.plugin import dtos
from cactus_runner.plugin.backends.models import RunnerBackendTestContext


def generate_plugin_context(test_procedure: ActiveTestProcedure) -> RunnerBackendTestContext:
    """Creates a suitable immutable context to be commmunicated with harness backend plugins."""
    return RunnerBackendTestContext(
        name=test_procedure.name,
        definition=test_procedure.definition,
        csip_aus_version=test_procedure.csip_aus_version,
        initialised_at=test_procedure.initialised_at,
        started_at=test_procedure.started_at,
        client_certificate_type=test_procedure.client_certificate_type,
        client_aggregator_id=test_procedure.client_aggregator_id,
        client_lfdi=test_procedure.client_lfdi,
        client_sfdi=test_procedure.client_sfdi,
        run_id=test_procedure.run_id,
        pen=test_procedure.pen,
        subscription_domain=test_procedure.subscription_domain,
        is_static_url=test_procedure.is_static_url,
        run_group_id=test_procedure.run_group_id,
        run_group_name=test_procedure.run_group_name,
        user_id=test_procedure.user_id,
        user_name=test_procedure.user_name,
        communications_disabled=test_procedure.communications_disabled,
    )


@runtime_checkable
class RunnerBackend(Protocol):
    """Backend abstraction consumed by checks, actions, status generation, event processing, and test execution.

    Implementations translate between their native storage or transport representation and the runner DTOs.
    All string IDs passed to and returned from these methods are string-encoded integers unless otherwise noted.

    Implementations may be backed by:

    - Envoy SQLAlchemy + Admin REST API
    - gRPC services
    - External APIs
    - Test doubles / mocks
    """

    # -----------------------------------------------------------------
    # Common lifecycle management hooks
    # -----------------------------------------------------------------

    async def set_test_context(self, context: RunnerBackendTestContext) -> None:
        """Receives the immutable test-run context at test initialisation time.

        Called once per test before any other backend methods. Implementations should store this
        context if they need to condition behaviour on test metadata (e.g. client identity, run ID).

        Args:
            context: Immutable snapshot of the active test procedure's identity and configuration.
        """

    # ------------------------------------------------------------------
    # Sites
    # ------------------------------------------------------------------

    async def get_active_site(
        self,
        include_der_settings: bool = False,
    ) -> dtos.Site | None:
        """Returns the active site, defined as the most recently modified EndDevice known to the backend.

        Args:
            include_der_settings: If True, the returned Site should have DER sub-resources (rating,
                setting, status) populated where available. Implementations may eagerly load or lazily
                resolve these as appropriate.

        Returns:
            The most recently modified Site, or None if no sites are registered.
        """
        ...

    async def get_all_sites(
        self,
    ) -> Sequence[dtos.Site]:
        """Returns all registered sites.

        Returns:
            All known Site entries. Order is implementation-defined but should be stable.
        """
        ...

    async def register_site(
        self,
        site: dtos.SiteWrite,
    ) -> None:
        """Persists a new site in the backend.

        Implementations should be idempotent where possible — if a site with the same
        identity (e.g. lfdi) already exists, it is acceptable to skip creation silently
        rather than raise.

        Args:
            site: The site definition to register.
        """
        ...

    async def update_site_post_rate(
        self,
        site_id: str,
        post_rate_seconds: int,
    ) -> None:
        """Updates the postRate interval for a site.

        Only the postRate field should be modified; all other site fields must remain unchanged.

        Args:
            site_id: The string-encoded identifier of the site to update.
            post_rate_seconds: The new postRate interval in seconds.

        Raises:
            Exception: If the site does not exist or the update cannot be applied.
        """
        ...

    # ------------------------------------------------------------------
    # DER
    # ------------------------------------------------------------------

    async def get_der_settings(
        self,
        site_id: str,
    ) -> dtos.SiteDERSetting | None:
        """Returns the DERSettings for the given site.

        Cactus runner assumes at most one DER is registered per site for any given test.

        Args:
            site_id: The string-encoded identifier of the site.

        Returns:
            The site's SiteDERSetting, or None if no DER settings have been recorded.
        """
        ...

    async def get_der_capability(
        self,
        site_id: str,
    ) -> dtos.SiteDERRating | None:
        """Returns the DERCapability (rating) for the given site.

        Cactus runner assumes at most one DER is registered per site for any given test.

        Args:
            site_id: The string-encoded identifier of the site.

        Returns:
            The site's SiteDERRating, or None if no DER capability has been recorded.
        """
        ...

    async def get_der_status(
        self,
        site_id: str,
    ) -> dtos.SiteDERStatus | None:
        """Returns the most recent DERStatus for the given site.

        Cactus runner assumes at most one DER is registered per site for any given test.

        Args:
            site_id: The string-encoded identifier of the site.

        Returns:
            The site's SiteDERStatus, or None if no DER status has been recorded.
        """
        ...

    # ------------------------------------------------------------------
    # Readings
    # ------------------------------------------------------------------

    async def get_site_reading_types(
        self,
        site_ids: Sequence[str] | None,
    ) -> Sequence[dtos.SiteReadingType]:
        """Returns SiteReadingTypes, optionally scoped to specific sites.

        Args:
            site_ids: If provided, only reading types belonging to these sites are returned.
                Pass None to return all reading types. An empty list should return nothing.

        Returns:
            All matching SiteReadingType entries.
        """
        ...

    async def get_site_readings(
        self,
        site_reading_type_ids: Sequence[str],
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Sequence[dtos.SiteReading]:
        """Returns readings belonging to the specified SiteReadingTypes, optionally within a time window.

        When both ``start_time`` and ``end_time`` are provided, implementations should return readings
        whose active period overlaps the window — specifically, readings where
        ``time_period_start + time_period_duration`` falls within ``[start_time, end_time]``.
        Readings that partially overlap the window boundary should be excluded.

        Args:
            site_reading_type_ids: IDs of the SiteReadingTypes whose readings should be returned.
            start_time: Optional start of the time window. If None, no lower bound is applied.
            end_time: Optional end of the time window. If None, no upper bound is applied.

        Returns:
            All SiteReadings matching the supplied type IDs and time window.
        """
        ...

    # ------------------------------------------------------------------
    # DER Programs
    # ------------------------------------------------------------------

    async def get_site_control_groups(
        self,
        fsa_ids: Sequence[str] | None = None,
    ) -> Sequence[dtos.SiteControlGroup]:
        """Returns DERPrograms, optionally filtered by Function Set Assignment ID.

        Args:
            fsa_ids: If provided, only groups whose fsa_id appears in this list are returned.
                Pass None to return all groups. An empty list should return nothing.

        Returns:
            All matching SiteControlGroup entries.
        """
        ...

    async def create_site_control_group(
        self,
        group: dtos.SiteControlGroupWrite,
    ) -> str:
        """Creates a new DERProgram.

        Args:
            group: The DERProgram definition to create.
                If group.display_id is not None, then the generated mRID for the resulting SiteControlGroup
                internal model needs to be repeatable for it, i.e. it needs to be deterministic based on
                display_id value when used as a seed in mRID generation.

        Returns:
            The backend-assigned site_control_group_id for the newly created group.

        Raises:
            Exception: If the group cannot be created (e.g. conflicting primacy or backend error).
        """
        ...

    async def update_site_control_group(
        self,
        group_id: str,
        group: dtos.SiteControlGroupWrite,
    ) -> None:
        """Updates an existing DERProgram, replacing all mutable fields.

        Args:
            group_id: The string-encoded identifier of the group to update.
            group: The updated DERProgram definition. All fields are replaced.

        Raises:
            Exception: If the group does not exist or the update cannot be applied.
        """
        ...

    async def remove_function_set_assignment(
        self,
        fsa_id: str,
    ) -> None:
        """Removes a Function Set Assignment from all DERPrograms that reference it.

        Implementations should locate every SiteControlGroup whose fsa_id matches and
        update them to clear the association (e.g. set fsa_id to None).

        Args:
            fsa_id: The string-encoded FSA ID to remove.
        """
        ...

    # ------------------------------------------------------------------
    # DER Controls
    # ------------------------------------------------------------------

    async def create_site_control(
        self,
        *,
        site_control_group_id: str,
        control: dtos.SiteControlWrite,
    ) -> None:
        """Creates a DERControl under the specified DERProgram.

        Args:
            control: The DERControl definition, including site, timing, and limit fields.
            site_control_group_id: The string-encoded ID of the parent DERProgram.

        Raises:
            Exception: If the control cannot be created or the parent group does not exist.
        """
        ...

    async def get_site_controls(
        self,
    ) -> Sequence[dtos.SiteControl]:
        """Returns all DERControls relevant to the test, across their full lifecycle.

        Implementations must include controls that are active, scheduled, completed, cancelled,
        or archived. When in doubt, return everything and allow the calling check logic to filter.

        Returns:
            All SiteControl entries recorded during or before the test.
        """
        ...

    async def cancel_active_site_controls(
        self,
    ) -> None:
        """Cancels all currently active and scheduled DERControls across every DERProgram.

        Implementations should ensure that no controls remain in an active or future-scheduled
        state after this call returns. The mechanism (e.g. delete-in-range, explicit cancel)
        is implementation-specific.

        Raises:
            Exception: If any cancellation call fails.
        """
        ...

    async def delete_site_controls_for_group(self, site_control_group_id: str) -> None:
        """Delete all controls for the given group, if can be differentiated from cancelling.

        Args:
            site_control_group_id: id of group the controls will belong to

        Raises:
            Exception: If a cancellation request fails.
        """
        ...

    # ------------------------------------------------------------------
    # Default DER Controls
    # ------------------------------------------------------------------

    async def set_site_control_default(
        self,
        *,
        site_control_group_id: str,
        default: dtos.SiteControlGroupDefaultWrite,
    ) -> None:
        """Sets or updates the DefaultDERControl for a DERProgram.

        Fields set to None in ``default`` should be treated as "no change" (leave the existing
        server value intact) unless ``default.cancelled`` is True, in which case those fields
        should be explicitly cleared to None on the backend.

        Args:
            site_control_group_id: The string-encoded ID of the DERProgram to update.
            default: The default limit values to apply. Set ``cancelled=True`` to explicitly
                clear all unset limit fields rather than leaving them unchanged.

        Raises:
            Exception: If the group does not exist or the update cannot be applied.
        """
        ...

    async def get_site_control_defaults(
        self,
    ) -> Sequence[dtos.SiteControlGroupDefault]:
        """Returns the current and historical DefaultDERControl values across all DERPrograms.

        Returns:
            All SiteControlGroupDefault entries recorded, in no guaranteed order.
        """
        ...

    # ------------------------------------------------------------------
    # Responses
    # ------------------------------------------------------------------

    async def get_site_control_responses(
        self,
    ) -> Sequence[dtos.SiteControlResponse]:
        """Returns all DERControl responses submitted by devices during the test.

        Returns:
            All SiteControlResponse entries, in no guaranteed order.
        """
        ...

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    async def get_subscription(self, subscription_id: str) -> dtos.Subscription | None:
        """Returns a single subscription by ID.

        Args:
            subscription_id: The string-encoded subscription identifier.

        Returns:
            The matching Subscription, or None if no subscription with that ID exists.
        """
        ...

    async def get_subscriptions(self, aggregator_client_id: str | None = None) -> Sequence[dtos.Subscription]:
        """Returns all subscriptions, optionally filtered to a single aggregator.

        Args:
            aggregator_client_id: If provided, only subscriptions belonging to this aggregator
                are returned. Pass None to return all subscriptions.

        Returns:
            All matching Subscription entries.
        """
        ...

    async def get_notification_logs(
        self,
    ) -> Sequence[dtos.TransmitNotificationLog]:
        """Returns all notification transmission log entries recorded during the test.

        Returns:
            All TransmitNotificationLog entries, in no guaranteed order.
        """
        ...

    # ------------------------------------------------------------------
    # Runtime Configuration
    # ------------------------------------------------------------------

    async def get_runtime_config(
        self,
    ) -> dtos.RuntimeConfig:
        """Returns the current runtime configuration from the backend.

        Returns:
            A RuntimeConfig reflecting the backend's current server configuration state.
        """
        ...

    async def update_runtime_config(
        self,
        config: dtos.RuntimeConfigWrite,
    ) -> None:
        """Applies runtime configuration changes to the backend.

        Fields set to None should be treated as "no change" — only explicitly set fields
        should be applied.

        Args:
            config: The configuration fields to update.

        Raises:
            Exception: If the configuration cannot be applied.
        """
        ...

    # ------------------------------------------------------------------
    # Administrative cleanup
    # ------------------------------------------------------------------

    async def delete_all_site_control_groups(
        self,
    ) -> None:
        """Deletes all DERPrograms and all downstream resources they own.

        This covers DERControls, DefaultDERControls, and Function Set Assignments.
        Intended for between-test cleanup in playlist runs to reset control state without
        tearing down the full backend instance.

        Raises:
            Exception: If any resource cannot be deleted.
        """
        ...

    async def delete_site(
        self,
        site_id: str,
    ) -> None:
        """Deletes a site and any resources owned exclusively by it.

        Args:
            site_id: The string-encoded identifier of the site to delete.

        Raises:
            Exception: If the site does not exist or cannot be deleted.
        """
        ...

    # ---------------------------------------------------------------
    # Helper methods
    # -----------------------------------------------------------------

    async def parse_subscription_href(self, href: str) -> dtos.SubscriptionHref:
        """Parses a subscription resource href into its component parts.

        This is intentionally an instance method rather than a static one — some backends may
        need to resolve internal resources to fulfil the DTO contract (e.g. looking up a site
        by scoped ID). Implementations should propagate parse errors rather than swallowing them.

        Args:
            href: A subscription resource href as provided by the test definition.

        Returns:
            A SubscriptionHref containing the resolved resource_type, scoped_site_id,
            and resource_id.

        Raises:
            InvalidMappingError: If the href cannot be parsed into a valid subscription
                resource reference.
        """
        ...


# ---------------------------------------------------------------------------
# Ordering helpers
#
# Backend implementations make no ordering guarantees on their Sequence
# return values. These module-level helpers wrap backend.get_* calls and
# apply the canonical sort that the original envoy_common queries enforced,
# ensuring consuming logic (checks, actions) always sees a stable order
# regardless of which backend is in use.
# ---------------------------------------------------------------------------


async def get_site_readings_ordered(
    backend: RunnerBackend,
    site_reading_type_ids: Sequence[str],
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> Sequence[dtos.SiteReading]:
    """Returns SiteReadings ordered by created_time ascending.

    Mirrors the original ``envoy_common.get_site_readings`` ordering
    (``ORDER BY created_time ASC``).

    Args:
        backend: The active runner backend.
        site_reading_type_ids: IDs of the SiteReadingTypes whose readings should be returned.
        start_time: Optional start of the time window; passed through to the backend.
        end_time: Optional end of the time window; passed through to the backend.

    Returns:
        All matching SiteReading entries ordered by created_time ascending.
    """
    readings = await backend.get_site_readings(site_reading_type_ids, start_time=start_time, end_time=end_time)
    return sorted(readings, key=attrgetter("created_time"))


async def get_site_reading_types_ordered(
    backend: RunnerBackend,
    site_ids: Sequence[str] | None = None,
) -> Sequence[dtos.SiteReadingType]:
    """Returns SiteReadingTypes ordered by created_time ascending.

    Mirrors the original ``envoy_common.get_csip_aus_site_reading_types_partitioned``
    ordering (``ORDER BY SiteReadingType.created_time ASC``). Callers do not
    need to sort the result themselves.

    Args:
        backend: The active runner backend.
        site_ids: Passed through to ``backend.get_site_reading_types``. Pass
            ``None`` to return all reading types.

    Returns:
        All matching SiteReadingType entries ordered by created_time ascending.
    """
    types = await backend.get_site_reading_types(site_ids=site_ids)
    return sorted(types, key=attrgetter("created_time"))


async def get_site_control_groups_ordered(
    backend: RunnerBackend,
    fsa_ids: Sequence[str] | None = None,
) -> Sequence[dtos.SiteControlGroup]:
    """Returns SiteControlGroups ordered by primacy ascending.

    This Mirrors the SEP2 ordering requirement assigned to the DERProgramLists

    Args:
        backend: The active runner backend.
        fsa_ids: The ids for all function set assignments that the DERPrograms will be assigned, or None for all.
    """
    site_control_groups = await backend.get_site_control_groups(fsa_ids=fsa_ids)
    return sorted(site_control_groups, key=attrgetter("primacy"))
