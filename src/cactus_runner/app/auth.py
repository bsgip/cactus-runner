import logging

from aiohttp import web
from envoy.server.api.depends.lfdi_auth import LFDIAuthDepends

from cactus_runner.app.shared import APPKEY_INITIALISED_CERTS

logger = logging.getLogger(__name__)


def request_is_authorized(request: web.Request) -> bool:
    """Returns true if the certificate in the request header matches the registered aggregator's certificate"""
    # Certificate forwarded https://kubernetes.github.io/ingress-nginx
    certificate = request.headers["ssl-client-cert"]
    initialised_certs = request.app[APPKEY_INITIALISED_CERTS]

    aggregator_lfdi = initialised_certs.aggregator_lfdi
    device_lfdi = initialised_certs.device_lfdi

    return lfdi_from_certificate_matches(
        certificate=certificate, aggregator_lfdi=aggregator_lfdi, device_lfdi=device_lfdi
    )


def lfdi_from_certificate_matches(certificate: str, aggregator_lfdi: str | None, device_lfdi: str | None) -> bool:
    incoming_lfdi = LFDIAuthDepends.generate_lfdi_from_pem(certificate)
    logger.debug(f"incoming_lfdi={incoming_lfdi}, aggregator_lfdi={aggregator_lfdi} device_lfdi={device_lfdi}")
    return (incoming_lfdi == aggregator_lfdi) or (incoming_lfdi == device_lfdi)
