"""Template for a backend test. Copy to tests/unit/test_<feature>.py (or
tests/integration/) and replace the body. NOT collected by pytest itself
(see tests/conftest.py `collect_ignore_glob`). One behavior per test; use the
client_factory / make_settings fixtures from conftest.py for API tests."""

from __future__ import annotations


def test_example_behavior() -> None:
    # Arrange
    left, right = 2, 3
    # Act
    total = left + right
    # Assert
    assert total == 5
