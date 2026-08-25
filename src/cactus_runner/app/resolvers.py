import logging
import secrets

from cactus_test_definitions import errors

from cactus_runner.models import ActiveTestProcedure

logger = logging.getLogger(__name__)

RANDOM_URI_ATTEMPTS = 20
RANDOM_URI_LENGTH = 16


def candidate_random_uri() -> str:
    return f"/{secrets.token_urlsafe(RANDOM_URI_LENGTH)}"


def resolve_random_uri(active_test_procedure: ActiveTestProcedure, randuri_key: str) -> str:
    random_uri_value = active_test_procedure.random_values.random_uri_by_key.get(randuri_key)
    if random_uri_value is not None:
        return random_uri_value

    # We need to generate a unique candidate value
    existing_random_uris = set(active_test_procedure.random_values.random_uri_by_key.values())
    for i in range(RANDOM_URI_ATTEMPTS):
        candidate_value = candidate_random_uri()
        if candidate_value not in existing_random_uris:
            active_test_procedure.random_values.random_uri_by_key[randuri_key] = candidate_value
            return candidate_value
        logger.warning(
            f"[{i}] Collision in randuri '{randuri_key}' with '{candidate_value}'."
            + f" There are {len(existing_random_uris)} existing entries."
        )

    raise errors.UnresolvableVariableError(
        f"Unable to resolve random uri {randuri_key} after {RANDOM_URI_ATTEMPTS} attempts"
    )
