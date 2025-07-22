from assertical.asserts.time import assert_nowish

from cactus_runner.app import resolvers


def test_resolve_named_variable_now():
    actual = resolvers.resolve_named_variable_now()
    assert actual.tzinfo
    assert_nowish(actual)
