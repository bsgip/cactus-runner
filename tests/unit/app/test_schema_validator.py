import pytest
from assertical.asserts.type import assert_list_type

from cactus_runner.app.schema_validator import validate_xml


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
