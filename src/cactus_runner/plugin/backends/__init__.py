from cactus_runner.models import ActiveTestProcedure, RunnerBackendTestContext


def generate_plugin_context(test_procedure: ActiveTestProcedure) -> RunnerBackendTestContext:
    """Creates a suitable immutable context to be commmunicated with harness backend plugins."""
    return RunnerBackendTestContext(
        name=test_procedure.name,
        definition=test_procedure.definition,
        csip_aus_version=test_procedure.csip_aus_version,
        initialised_at=test_procedure.initialised_at,
        started_at=test_procedure.started_at,
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


