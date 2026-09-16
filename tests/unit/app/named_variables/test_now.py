from assertical.asserts.time import assert_nowish

from cactus_runner.plugin.backends.resolver import resolve_named_variable_now


def test_resolve_named_variable_now():
    actual = resolve_named_variable_now()
    assert actual.tzinfo
    assert_nowish(actual)
