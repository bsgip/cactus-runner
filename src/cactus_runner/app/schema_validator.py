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


def validate_xml(xml: str) -> list[str]:
    """Validates an xml document / snippet as a valid CSIP Aus 1.2 XML snippet. Returns a list of any human
    readable schema validation errors. Empty list means that xml is schema valid"""

    try:
        xml_doc = etree.fromstring(xml)
    except Exception as exc:
        preview = xml[:32]
        logger.error(f"validate_xml: Failure parsing string starting '{preview}'... as XML", exc_info=exc)
        return [f"The provided body '{preview}'... does NOT parse as XML"]

    schema = csip_aus_schema()

    # Validate
    is_valid = schema.validate(xml_doc)
    if is_valid:
        return []
    else:
        return [f"{e.line}: {e.message}" for e in schema.error_log]  # type: ignore


def validate_proxy_request_schema(proxy_result: ProxyResult) -> list[str]:
    """Attempts to apply validate_xml to proxy_result's request body. Will not raise exceptions for decoding request
    body, these errors will instead be returned as a list of errors (similar to validate_xml)"""
    if len(proxy_result.request_body) == 0:
        return []

    encoding = "UTF-8" if proxy_result.request_encoding is None else proxy_result.request_encoding
    try:
        xml_string = proxy_result.request_body.decode(encoding=encoding)
    except Exception as exc:
        logger.error(f"Error interpreting request body as '{encoding}' text", exc_info=exc)
        return [f"Unable to interpret request body as '{encoding}' text"]

    return validate_xml(xml_string)
