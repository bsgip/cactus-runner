from assertical.asserts.time import assert_nowish
from cactus_runner.app.resolvers import resolve_named_variable_now


def test_resolve_named_variable_now():
    actual = resolve_named_variable_now()
    assert actual.tzinfo
    assert_nowish(actual)



class TestSetMaxVA:
    pass


class TestSetMaxVar:
    pass


class TestSetChargeRateW:
    pass


class TestSetMaxDischargeRateW:
    pass


class TestSetMaxWh:
    pass


class TestRtgMaxVA:
    pass


class TestRtgMaxVar:
    pass


class TestRtgMaxW:
    pass


class TestRtgMaxChargeRateW:
    pass


class TestRtgMaxDischargeRateW:
    pass


class TestRtgMaxWh:
    pass
