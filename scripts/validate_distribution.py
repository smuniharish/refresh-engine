"""Validate built distributions and smoke-test the wheel in a clean environment."""

from __future__ import annotations

import email
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

EXPECTED_DEPENDENCIES = {"apscheduler", "networkx", "structlog", "tenacity"}
FORBIDDEN_WHEEL_PARTS = {"postgres_store.py", "sqlite_store.py", "thread_scheduler.py"}


def _normalized_requirement_name(requirement: str) -> str:
    name = requirement.split(";", 1)[0].split("[", 1)[0]
    for separator in ("<", ">", "=", "!", "~"):
        name = name.split(separator, 1)[0]
    return name.strip().lower().replace("_", "-")


def _validate_wheel(wheel: Path, expected_version: str) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        forbidden = {
            part
            for part in FORBIDDEN_WHEEL_PARTS
            if any(name.endswith(part) for name in names)
        }
        if forbidden:
            raise RuntimeError(f"wheel contains private adapters: {sorted(forbidden)}")
        if not any(name.endswith("refresh_engine/py.typed") for name in names):
            raise RuntimeError("wheel does not contain the py.typed marker")
        if any(name.startswith(("tests/", "examples/", "scripts/")) for name in names):
            raise RuntimeError("wheel contains development-only source files")

        metadata_names = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise RuntimeError("wheel must contain exactly one METADATA file")
        metadata = email.message_from_bytes(archive.read(metadata_names[0]))

    if metadata["Name"] != "refresh-engine":
        raise RuntimeError(f"unexpected distribution name: {metadata['Name']}")
    if metadata["Version"] != expected_version:
        raise RuntimeError(f"unexpected distribution version: {metadata['Version']}")
    python_specifiers = {
        specifier.strip() for specifier in metadata["Requires-Python"].split(",")
    }
    if python_specifiers != {">=3.12", "<3.13"}:
        python_requirement = metadata["Requires-Python"]
        raise RuntimeError(f"unexpected Python requirement: {python_requirement}")
    if metadata["Author"] != "S. Muni Harish":
        raise RuntimeError(f"unexpected author: {metadata['Author']}")
    if metadata["License-Expression"] != "Apache-2.0":
        raise RuntimeError(
            f"unexpected license expression: {metadata['License-Expression']}"
        )

    dependencies = {
        _normalized_requirement_name(value)
        for value in metadata.get_all("Requires-Dist", [])
    }
    if dependencies != EXPECTED_DEPENDENCIES:
        raise RuntimeError(f"unexpected runtime dependencies: {sorted(dependencies)}")


def _validate_sdist(sdist: Path) -> None:
    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
    required_suffixes = {
        "LICENSE",
        "README.md",
        "pyproject.toml",
        "src/refresh_engine/py.typed",
    }
    missing = {
        suffix
        for suffix in required_suffixes
        if not any(name.endswith(suffix) for name in names)
    }
    if missing:
        raise RuntimeError(f"sdist is missing required files: {sorted(missing)}")


def _smoke_test_wheel(wheel: Path, expected_version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="refresh-engine-wheel-") as directory:
        environment = Path(directory)
        command_environment = os.environ | {"UV_SYSTEM_CERTS": "true"}
        subprocess.run(
            ["uv", "venv", "--python", "3.12", str(environment)],
            check=True,
            env=command_environment,
        )
        executable = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        python = environment / executable
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), str(wheel)],
            check=True,
            env=command_environment,
        )
        smoke = f"""
import asyncio
import importlib.util

import refresh_engine
from refresh_engine import (
    DiscoveryResult,
    InMemoryStateStore,
    RefreshEngine,
    Resource,
    ResourceSnapshot,
    StateStore,
    StateTransaction,
)

class Source:
    async def discover(self):
        async def resources():
            yield Resource("smoke")
        return DiscoveryResult(resources())

    async def snapshot(self, resource):
        return ResourceSnapshot(resource.resource_id, content="ok")

async def operation(resource, snapshot, action, request):
    return None

async def main():
    engine = RefreshEngine(Source(), operation)
    assert isinstance(engine.store, InMemoryStateStore)
    result = await engine.refresh()
    assert result.refreshed_count == 1
    await engine.close()

assert refresh_engine.__version__ == {expected_version!r}
assert StateStore and StateTransaction
assert not hasattr(refresh_engine, "SQLiteStateStore")
assert not hasattr(refresh_engine, "PostgresStateStore")
assert importlib.util.find_spec("refresh_engine.stores.sqlite") is None
asyncio.run(main())
print("clean wheel smoke test passed")
"""
        subprocess.run([str(python), "-c", smoke], check=True)


def main() -> None:
    distribution_directory = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    with Path("pyproject.toml").open("rb") as project_file:
        expected_version = tomllib.load(project_file)["project"]["version"]
    wheels = list(distribution_directory.glob("refresh_engine-*.whl"))
    sdists = list(distribution_directory.glob("refresh_engine-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError("expected exactly one wheel and one source distribution")

    wheel = wheels[0].resolve()
    _validate_wheel(wheel, expected_version)
    _validate_sdist(sdists[0])
    _smoke_test_wheel(wheel, expected_version)
    print("distribution validation passed")


if __name__ == "__main__":
    main()
