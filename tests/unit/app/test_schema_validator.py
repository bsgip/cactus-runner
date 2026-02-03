import unittest.mock as mock

import pytest
from assertical.asserts.type import assert_list_type

from cactus_runner.app.proxy import ProxyResult
from cactus_runner.app.schema_validator import validate_proxy_request_schema


def make_proxy_result(body: bytes) -> ProxyResult:
    """Helper to create a ProxyResult with just the body we care about for validation"""
    return ProxyResult("", "", body, None, {}, mock.MagicMock())


@pytest.mark.parametrize(
    "xml",
    [
        """
<DERControlList
    xmlns="urn:ieee:std:2030.5:ns"
    xmlns:csipaus="https://csipaus.org/ns" all="2" href="/derp/0/derc" results="1">
    <DERControl replyTo="/rsp" responseRequired="03">
        <mRID>ABCDEF0123456789</mRID>
        <description>Example DERControl 1</description>
        <creationTime>1605621300</creationTime>
        <EventStatus>
            <currentStatus>0</currentStatus>
            <dateTime>1605621300</dateTime>
            <potentiallySuperseded>false</potentiallySuperseded>
        </EventStatus>
        <interval>
            <duration>86400</duration>
            <start>1605621600</start>
        </interval>
        <DERControlBase>
            <csipaus:opModImpLimW>
                <multiplier>0</multiplier>
                <value>20000</value>
            </csipaus:opModImpLimW>
            <csipaus:opModExpLimW>
                <multiplier>0</multiplier>
                <value>5000</value>
            </csipaus:opModExpLimW>
            <csipaus:opModGenLimW>
                <multiplier>0</multiplier>
                <value>5000</value>
            </csipaus:opModGenLimW>
            <csipaus:opModLoadLimW>
                <multiplier>0</multiplier>
                <value>20000</value>
            </csipaus:opModLoadLimW>
        </DERControlBase>
    </DERControl>
</DERControlList>""",
        """
<ConnectionPoint xmlns="https://csipaus.org/ns">
    <connectionPointId>1234567890</connectionPointId>
</ConnectionPoint>""",
        """
<DERControlBase xmlns="urn:ieee:std:2030.5:ns" xmlns:csipaus="https://csipaus.org/ns">
    <csipaus:opModImpLimW>
        <multiplier>0</multiplier>
        <value>20000</value>
    </csipaus:opModImpLimW>
</DERControlBase>""",
    ],
)
def test_validate_proxy_request_schema_valid_xml(xml: str):
    """Tests validate_proxy_request_schema against various valid CSIP-Aus XML snippets"""
    result = validate_proxy_request_schema(make_proxy_result(xml.encode("utf-8")))
    assert isinstance(result, list)
    assert len(result) == 0, "\n".join(result)


@pytest.mark.parametrize(
    "xml",
    [
        "123451",
        '{"foo": 123}',
        '<ConnectionPoint xmlns="https://csipaus.org/ns"><c',
    ],
)
def test_validate_proxy_request_schema_not_xml(xml: str):
    """Tests validate_proxy_request_schema can handle a variety of "not xml" strings and fail appropriately"""
    result = validate_proxy_request_schema(make_proxy_result(xml.encode("utf-8")))
    assert_list_type(str, result, count=1)  # We expect exactly 1 error if the XML is bad


@pytest.mark.parametrize(
    "xml",
    [
        """
<ConnectionPoint xmlns="https://csipaus.org/ns">
    <connectionPointId>1234567890</connectionPointId>
    <extraElement/>
</ConnectionPoint>
""",  # Extra elements
        """
<DERControlBase xmlns="urn:ieee:std:2030.5:ns" xmlns:csipaus="https://csipaus.org/ns">
    <csipaus:opModImpLimW>
        <value>20000</value>
        <multiplier>0</multiplier>
    </csipaus:opModImpLimW>
</DERControlBase>""",  # Element ordering
    ],
)
def test_validate_proxy_request_schema_schema_invalid(xml: str):
    """Tests validate_proxy_request_schema can handle a variety of xml strings that fail schema validation"""
    result = validate_proxy_request_schema(make_proxy_result(xml.encode("utf-8")))
    assert_list_type(str, result)
    assert len(result) > 0


def test_validate_proxy_request_schema_empty_body():
    """Tests that an empty body returns no errors"""
    result = validate_proxy_request_schema(make_proxy_result(bytes()))
    assert_list_type(str, result, count=0)


@pytest.mark.parametrize(
    "encoding, xml_declaration, should_pass",
    [
        ("utf-8", None, True),  # lxml defaults to UTF-8
        ("utf-8", "UTF-8", True),
        ("utf-16", "UTF-16", True),
        ("utf-16", None, True),
        ("utf-32", "UTF-32", True),
        ("utf-32", None, True),
        ("utf-8", "UTF-16", False),  # Mismatched: bytes are UTF-8 but declaration claims UTF-16
    ],
)
def test_validate_proxy_request_schema_encoding(encoding: str, xml_declaration: str | None, should_pass: bool):
    """Tests that various encodings are handled correctly by lxml's encoding detection"""

    valid_cp_xml = """<ConnectionPoint xmlns="https://csipaus.org/ns">
        <connectionPointId>1234567890</connectionPointId>
    </ConnectionPoint>"""

    if xml_declaration:
        xml = f'<?xml version="1.0" encoding="{xml_declaration}"?>{valid_cp_xml}'
    else:
        xml = valid_cp_xml

    body = xml.encode(encoding)
    result = validate_proxy_request_schema(make_proxy_result(body))

    if should_pass:
        assert_list_type(str, result, count=0)
    else:
        assert len(result) > 0  # Should have parse errors
