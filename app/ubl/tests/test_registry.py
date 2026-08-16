import pytest

from ubl.builder.registry import get_renderer, supported_versions


def test_supported_versions_includes_2025():
    assert '2025' in supported_versions()


def test_get_renderer_returns_callable():
    renderer = get_renderer('2025')
    assert callable(renderer)


def test_unknown_version_raises():
    with pytest.raises(ValueError, match='Nepoznata UBL verzija'):
        get_renderer('2099')
