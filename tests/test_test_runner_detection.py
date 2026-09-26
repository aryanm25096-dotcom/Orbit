"""
tests/test_test_runner_detection.py — Unit tests for ToolGateway test runner auto-detection
=============================================================================================
PRD §4.4, §7:
  Test command detection logic:
    - pytest.ini / pyproject.toml -> 'pytest'
    - package.json with 'test' script -> 'npm test'
    - Cargo.toml -> 'cargo test'
    - Makefile with 'test' target -> 'make test'
    - fallback -> 'pytest'
"""

import json
from pathlib import Path
import pytest

from gateway.gateway import ToolGateway, detect_test_command


def test_detect_pytest_ini(tmp_path: Path):
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")
    assert detect_test_command(tmp_path) == "pytest"


def test_detect_pyproject_toml(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\nminversion = '6.0'\n", encoding="utf-8")
    assert detect_test_command(tmp_path) == "pytest"


def test_detect_package_json_with_test(tmp_path: Path):
    pkg = {
        "name": "sample-project",
        "scripts": {
            "test": "jest",
            "build": "tsc"
        }
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    assert detect_test_command(tmp_path) == "npm test"


def test_detect_package_json_without_test(tmp_path: Path):
    pkg = {
        "name": "sample-project",
        "scripts": {
            "build": "tsc"
        }
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    # Fallback to pytest when no 'test' script is defined
    assert detect_test_command(tmp_path) == "pytest"


def test_detect_cargo_toml(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'sample'\nversion = '0.1.0'\n", encoding="utf-8")
    assert detect_test_command(tmp_path) == "cargo test"


def test_detect_makefile_with_test_target(tmp_path: Path):
    content = (
        "build:\n"
        "\techo 'building'\n\n"
        "test:\n"
        "\techo 'running tests'\n"
    )
    (tmp_path / "Makefile").write_text(content, encoding="utf-8")
    assert detect_test_command(tmp_path) == "make test"


def test_detect_makefile_without_test_target(tmp_path: Path):
    content = (
        "build:\n"
        "\techo 'building'\n\n"
        "clean:\n"
        "\trm -rf dist\n"
    )
    (tmp_path / "Makefile").write_text(content, encoding="utf-8")
    assert detect_test_command(tmp_path) == "pytest"


def test_detect_empty_directory_fallback(tmp_path: Path):
    assert detect_test_command(tmp_path) == "pytest"


def test_gateway_run_tests_auto_detects(tmp_path: Path):
    """Confirm ToolGateway.run_tests() auto-detects and charges exactly 1 call."""
    gw = ToolGateway(workspace_root=tmp_path)
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_sample.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    res = gw.run_tests()  # test_command=None -> auto-detect
    assert res.passed is True
    assert res.command == "pytest"
    assert len(gw.log) == 1
