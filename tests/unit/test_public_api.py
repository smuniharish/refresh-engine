import importlib
import importlib.metadata
import inspect

import pytest

import refresh_engine

PUBLIC_MODULES = (
    "refresh_engine.contracts",
    "refresh_engine.engine",
    "refresh_engine.errors",
    "refresh_engine.events",
    "refresh_engine.fingerprints",
    "refresh_engine.models",
    "refresh_engine.observability",
    "refresh_engine.scheduling",
    "refresh_engine.selectors",
    "refresh_engine.stores",
)


def test_all_is_sorted_unique_and_public() -> None:
    names = refresh_engine.__all__
    assert names == sorted(names)
    assert len(names) == len(set(names))
    assert not [name for name in names if name.startswith("_")]


def test_every_exported_name_resolves() -> None:
    for name in refresh_engine.__all__:
        assert getattr(refresh_engine, name) is not None, name


@pytest.mark.parametrize("module_name", PUBLIC_MODULES)
def test_public_modules_are_reexported(module_name: str) -> None:
    module = importlib.import_module(module_name)
    for name in module.__all__:
        assert name in refresh_engine.__all__, f"{module_name}.{name}"
        assert getattr(refresh_engine, name) is getattr(module, name)


def test_every_exported_name_comes_from_a_public_module() -> None:
    exported = {
        name
        for module_name in PUBLIC_MODULES
        for name in importlib.import_module(module_name).__all__
    }
    assert exported == set(refresh_engine.__all__)


def test_exported_objects_are_documented() -> None:
    for name in refresh_engine.__all__:
        value = getattr(refresh_engine, name)
        documentation = getattr(value, "__doc__", None)
        if inspect.isclass(value) or inspect.isfunction(value):
            assert documentation, name
            assert value.__module__.startswith("refresh_engine."), name
            assert not value.__module__.startswith("refresh_engine._"), name


def test_version_matches_distribution_metadata() -> None:
    assert refresh_engine.__version__ == importlib.metadata.version("refresh-engine")
