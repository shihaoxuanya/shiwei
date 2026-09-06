"""Host-independent regression coverage for the native packaging verifier."""
import importlib.util
from pathlib import Path

import pytest

script = Path(__file__).resolve().parents[3] / "scripts/verify-macos-bundle.py"
spec = importlib.util.spec_from_file_location("mac_verifier", script)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


@pytest.mark.parametrize("command", ["cmd LC_BUILD_VERSION\n minos 14.0\n sdk 26.0", "cmd LC_VERSION_MIN_MACOSX\n version 11.0\n sdk 14.0"])
def test_native_minimum_system_accepts_supported_deployment_targets(command):
    verifier.validate_load_commands(command)


@pytest.mark.parametrize("version", ["14.1", "15.0", "26.0"])
def test_native_dependencies_cannot_silently_raise_minimum_system(version):
    with pytest.raises(ValueError, match="newer"):
        verifier.validate_load_commands(f"cmd LC_BUILD_VERSION\n minos {version}\n sdk 26.0")


@pytest.mark.parametrize("dependency", ["/Users/runner/work/.venv/libpython.dylib", "/opt/homebrew/lib/libomp.dylib", "/tmp/build/native.dylib"])
def test_bundle_rejects_build_machine_dependencies(dependency):
    with pytest.raises(ValueError, match="absolute path"):
        verifier.validate_dependencies(f"binary:\n {dependency} (compatibility version 1.0.0)")


def test_bundle_accepts_system_and_relocatable_dependencies():
    verifier.validate_dependencies("binary:\n /usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n @rpath/libpython.dylib (compatibility version 1.0.0)\n @loader_path/numpy/native.so (compatibility version 1.0.0)")
