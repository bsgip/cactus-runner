import logging

from aiohttp import web
from envoy.server.api.depends.lfdi_auth import LFDIAuthDepends

from cactus_runner.app.shared import APPKEY_INITIALISED_CERTS

logger = logging.getLogger(__name__)


def request_is_authorized(request: web.Request) -> bool:
    """Returns true if the certificate in the request header matches the registered aggregator's certificate"""
    # Certificate forwarded https://kubernetes.github.io/ingress-nginx
    incoming_certificate = request.headers["ssl-client-cert"]
    expected_lfdi = request.app[APPKEY_INITIALISED_CERTS].client_lfdi

    incoming_lfdi = LFDIAuthDepends.generate_lfdi_from_pem(incoming_certificate)

    return incoming_lfdi == expected_lfdi
