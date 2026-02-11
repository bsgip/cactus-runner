import logging
from functools import lru_cache
from pathlib import Path

from lxml import etree

import cactus_runner.schema.csipaus12 as csipaus12
from cactus_runner.app.proxy import ProxyResult

logger = logging.getLogger(__name__)

CSIP_AUS_12_DIR = Path(csipaus12.__file__).parent


class LocalXsdResolver(etree.Resolver):
    """Finds specific XSD files in our local schema directory"""

    def resolve(self, url, id, context):
        if url == "sep.xsd":
            return self.resolve_filename(str(CSIP_AUS_12_DIR / "sep.xsd"), context)
        elif url == "csipaus-core.xsd":
            return self.resolve_filename(str(CSIP_AUS_12_DIR / "csipaus-core.xsd"), context)
        elif url == "csipaus-ext.xsd":
            return self.resolve_filename(str(CSIP_AUS_12_DIR / "csipaus-ext.xsd"), context)
        return None


@lru_cache
def csip_aus_schema() -> etree.XMLSchema:
    """Generates a etree.XMLSchema that's loaded with the CSIP Aus XSD document (which incorporates sep2)"""

    # Register the custom resolver
    parser = etree.XMLParser(load_dtd=True, no_network=True)
    parser.resolvers.add(LocalXsdResolver())

    # Load schema
    with open(CSIP_AUS_12_DIR / "csipaus-core.xsd", "r") as fp:
        xsd_content = fp.read()
    schema_root = etree.XML(xsd_content, parser)
    return etree.XMLSchema(schema_root)


def validate_proxy_request_schema(proxy_result: ProxyResult) -> list[str]:
    """Validates proxy_result's request body as CSIP Aus 1.2 XML. Returns a list of any human
    readable schema validation errors. Empty list means that xml is schema valid"""

    if len(proxy_result.request_body) == 0:
        return []

    try:
        # Pass bytes directly - lxml handles encoding detection from XML declaration, fallback to UTF8
        xml_doc = etree.fromstring(proxy_result.request_body)
    except Exception as exc:
        preview = proxy_result.request_body[:32].decode("utf-8", errors="replace")
        logger.error(f"Failure parsing request body starting with '{preview}'... as XML", exc_info=exc)
        return [f"The provided body '{preview}'... does NOT parse as XML"]

    schema = csip_aus_schema()
    if schema.validate(xml_doc):
        return []

    return [f"{e.line}: {e.message}" for e in schema.error_log]  # type: ignore
