import unittest.mock as mock

import pytest
from assertical.asserts.type import assert_list_type

from cactus_runner.app.proxy import ProxyResult
from cactus_runner.app.schema_validator import (
    validate_proxy_request_schema,
    validate_xml,
)


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
def test_validate_xml_valid_xml(xml):
    """Tests validate_xml against various valid CSIP-Aus XML snippets"""
    result = validate_xml(xml)
    assert isinstance(result, list)
    assert len(result) == 0, "\n".join(result)


@pytest.mark.parametrize(
    "xml",
    [
        "",
        "123451",
        '{"foo": 123}',
        '<ConnectionPoint xmlns="https://csipaus.org/ns"><c',
    ],
)
def test_validate_xml_not_xml(xml):
    """Tests validate_xml can handle a variety of "not xml" strings and fail appropriately"""
    result = validate_xml(xml)
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
def test_validate_xml_schema_invalid(xml):
    """Tests validate_xml can handle a variety of xml strings that fail schema validation"""
    result = validate_xml(xml)
    assert_list_type(str, result)
    assert len(result) > 0


@pytest.mark.parametrize(
    "input_encoding, output_encoding, is_valid_encoding",
    [
        ("utf-8", None, True),
        ("utf-8", "utf-8", True),
        ("utf-8", "UTF-8", True),
        ("utf-32", "utf-32", True),
        ("utf-32", None, False),
        ("utf-32", "utf-8", False),
        ("utf-8", "utf-32", False),
    ],
)
@mock.patch("cactus_runner.app.schema_validator.validate_xml")
def test_validate_proxy_request_schema_decoding(
    mock_validate_xml: mock.MagicMock, input_encoding: str, output_encoding: str | None, is_valid_encoding: bool
):
    """Tests the various ways that text decoding might succeed/fail"""
    body = "abc 123 _=@<>/'\""
    body_encoded = body.encode(input_encoding)

    mock_validate_result = mock.MagicMock()
    mock_validate_xml.return_value = mock_validate_result

    result = validate_proxy_request_schema(ProxyResult("", "", body_encoded, output_encoding, {}, mock.MagicMock()))

    if is_valid_encoding:
        mock_validate_xml.assert_called_once_with(body)
        assert result is mock_validate_result
    else:
        mock_validate_xml.assert_not_called()
        assert_list_type(str, result, count=1)


def test_validate_proxy_request_empty_body():
    result = validate_proxy_request_schema(ProxyResult("", "", bytes(), None, {}, mock.MagicMock()))
    assert_list_type(str, result, count=0)
