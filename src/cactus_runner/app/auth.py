import logging

from aiohttp import web
from envoy.server.api.depends.lfdi_auth import (
    LFDIAuthDepends,
    is_valid_lfdi,
    is_valid_pem,
    is_valid_sha256,
)

from cactus_runner.app.shared import APPKEY_INITIALISED_CERTS

logger = logging.getLogger(__name__)


def request_is_authorized(request: web.Request) -> bool:
    """Returns true if the certificate in the request header matches the registered aggregator's certificate"""
    # Certificate forwarded https://kubernetes.github.io/ingress-nginx
    incoming_certificate = request.headers.get("ssl-client-cert", "")
    expected_lfdi = request.app[APPKEY_INITIALISED_CERTS].client_lfdi

    if is_valid_pem(incoming_certificate):
        return LFDIAuthDepends.generate_lfdi_from_pem(incoming_certificate) == expected_lfdi
    elif is_valid_sha256(incoming_certificate):
        return LFDIAuthDepends.generate_lfdi_from_fingerprint(incoming_certificate) == expected_lfdi
    elif is_valid_lfdi(incoming_certificate):
        return incoming_certificate == expected_lfdi

    return False
