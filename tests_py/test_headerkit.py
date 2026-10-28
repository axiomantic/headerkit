import pytest
from headerkit import add, hello, version


def test_hello() -> None:
    greeting = hello()
    assert greeting == "hello from headerkit"


def test_version() -> None:
    v = version()
    assert v == "0.0.1"


def test_add_positive_numbers() -> None:
    result = add(10, 25)
    assert result == 35


def test_add_negative_and_zero() -> None:
    assert add(-5, 5) == 0
    assert add(0, 0) == 0
    assert add(-10, -20) == -30


def test_add_type_contract() -> None:
    # Negative control: invalid argument types should raise TypeError or fail conversion
    with pytest.raises((TypeError, ValueError)):
        add("invalid", 10)  # type: ignore[arg-type]
