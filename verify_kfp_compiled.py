#!/usr/bin/env python3
"""
Validate that Kubeflow Pipelines .py files are compiled to their .yaml counterparts.
Reads a JSON map (py_path -> yaml_path), compiles each .py with kfp, and diffs to the .yaml.
Exits with 1 on first mismatch or missing file.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast


def _read_lines(path: Path | str, error_label: str) -> list[str]:
    """Read file as UTF-8 lines; raise error on failure."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.readlines()
    except (OSError, UnicodeDecodeError) as e:
        raise RuntimeError(f"❌ Cannot read {error_label}: {e}") from e


def _compile_and_read_outputs(
    py_file: str,
    yaml_file: str,
    compiled_path: Path,
    extra_args: list[str],
) -> tuple[list[str], list[str]]:
    """Run kfp compile, then read yaml and compiled output."""
    cmd = [
        "kfp",
        "dsl",
        "compile",
        "--py",
        py_file,
        "--output",
        str(compiled_path),
        *extra_args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        error_msg = f"❌ kfp dsl compile failed for {py_file}"
        if result.stderr:
            error_msg += f"\n{result.stderr}"
        raise RuntimeError(error_msg)

    expected = _read_lines(yaml_file, f"YAML file {yaml_file}")
    actual = _read_lines(compiled_path, "compiled output")
    return expected, actual


def _print_out_of_date_fix_command(
    py_file: str,
    yaml_file: str,
    expected_lines: list[str],
    actual_lines: list[str],
    compile_args_str: str,
) -> None:
    """Print diff and the suggested kfp compile command."""
    print(f"❌ {yaml_file} is out of date with {py_file}")
    for line in difflib.unified_diff(
        expected_lines,
        actual_lines,
        fromfile=yaml_file,
        tofile="(compiled)",
        lineterm="",
    ):
        print(line)
    print("   → update by running:")
    extra = ""
    if compile_args_str:
        extra_parts = shlex.split(compile_args_str)
        extra = "  ".join(shlex.quote(arg) for arg in extra_parts)
    cmd_help = (
        f"     kfp dsl compile --py {shlex.quote(py_file)} "
        f"--output {shlex.quote(yaml_file)}{extra}"
    )
    print(cmd_help)


def _check_one(
    py_file: str,
    yaml_file: str,
    compiled_path: Path,
    extra_args: list[str],
    compile_args_str: str,
) -> None:
    """Compile py_file, diff to yaml_file; raise error on failure."""
    print(f"→ Checking {py_file} → {yaml_file}")

    if not Path(py_file).is_file():
        raise RuntimeError(f"❌ Python file not found: {py_file}")
    if not Path(yaml_file).is_file():
        raise RuntimeError(f"❌ Expected YAML missing: {yaml_file}")

    expected_lines, actual_lines = _compile_and_read_outputs(
        py_file, yaml_file, compiled_path, extra_args
    )

    if expected_lines != actual_lines:
        _print_out_of_date_fix_command(
            py_file,
            yaml_file,
            expected_lines,
            actual_lines,
            compile_args_str,
        )
        raise RuntimeError(f"❌ {yaml_file} is out of date with {py_file}")

    print(f"✅ {yaml_file} is up to date")


def _load_json_map(map_path: Path, map_file: str) -> object:
    """Load JSON from map_path; raise error on failure."""
    try:
        with open(map_path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise RuntimeError(f"❌ Cannot read map file {map_file}: {e}") from e
    except UnicodeDecodeError as e:
        raise RuntimeError(f"❌ Map file {map_file} is not valid UTF-8: {e}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"❌ Invalid JSON in {map_file}: {e}") from e


def _validate_mapping_entries(mapping: object) -> None:
    """Check mapping is a dict with string keys and values; raise error on failure."""
    if not isinstance(mapping, dict):
        raise RuntimeError(
            f"❌ Pipeline map must be a JSON object (py -> yaml), "
            f"got {type(mapping).__name__}"
        )
    for py_file, yaml_file in mapping.items():
        if not isinstance(py_file, str) or not isinstance(yaml_file, str):
            bad = "key" if not isinstance(py_file, str) else "value"
            raise RuntimeError(
                f"❌ Pipeline map entries must be strings (py -> yaml), "
                f"got non-string {bad}"
            )


def _load_and_validate_mapping(map_file: str) -> dict:
    """Load JSON map and validate; raise error on failure."""
    map_path = Path(map_file)
    if not map_path.is_file():
        raise RuntimeError(f"❌ Pipeline map file not found: {map_file}")

    mapping = _load_json_map(map_path, map_file)
    _validate_mapping_entries(mapping)

    return cast(dict, mapping)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Validate that KFP .py files are compiled to their .yaml counterparts.",
    )
    parser.add_argument(
        "--map-file",
        default=".github/kfp-pipelines-map.json",
        help="Path to JSON mapping file (py -> yaml)",
    )
    parser.add_argument(
        "--compile-args",
        default="",
        help="Extra arguments to pass to kfp dsl compile",
    )
    parser.add_argument(
        "--modified-only",
        action="store_true",
        help="Only validate entries whose files are Git-modified",
    )
    return parser.parse_args(argv)


def _normalize_base_branch(base_branch: str) -> str:
    """Qualify a bare branch name with 'origin/' so fetch and diff use the same ref."""
    if "/" not in base_branch:
        return f"origin/{base_branch}"
    return base_branch


def _resolve_base_branch() -> str:
    """Read the PR target branch from ``GITHUB_BASE_REF``.

    ``GITHUB_BASE_REF`` is set by GitHub Actions on ``pull_request``
    events and contains the **bare** branch name (e.g. ``main`` or
    ``develop``), without a remote prefix.  The value is returned
    as-is; callers that need a remote-qualified ref (``origin/main``)
    should pass it through :func:`_normalize_base_branch`.

    Raises ``RuntimeError`` when the variable is unset or empty
    (i.e. the workflow is not running on a ``pull_request`` event).
    """
    ref = os.environ.get("GITHUB_BASE_REF", "").strip()
    if not ref:
        raise RuntimeError(
            "❌ --modified-only requires GITHUB_BASE_REF environment variable "
            "(set automatically on pull_request events)"
        )
    return ref


def _fetch_base_branch(base_branch: str) -> None:
    """Fetch the base branch so it is available for git diff.

    *base_branch* must already be remote-qualified (e.g. ``origin/main``).
    """
    if "/" not in base_branch:
        raise RuntimeError(
            f"❌ base_branch must be remote-qualified (e.g. 'origin/main'), got: {base_branch!r}"
        )
    remote, branch = base_branch.split("/", 1)
    cmd = ["git", "fetch", remote, branch]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "❌ git is not installed or not on PATH"
        ) from e
    if result.returncode != 0:
        error_msg = f"❌ git fetch failed for {base_branch}"
        if result.stderr:
            error_msg += f"\n{result.stderr}"
        raise RuntimeError(error_msg)


def _get_git_modified_files(base_branch: str) -> set[str]:
    """Return the set of file paths modified between *base_branch* and HEAD."""
    normalized = _normalize_base_branch(base_branch)
    _fetch_base_branch(normalized)
    cmd = ["git", "diff", "--name-only", f"{normalized}...HEAD"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "❌ git is not installed or not on PATH"
        ) from e
    if result.returncode != 0:
        error_msg = "❌ git diff failed"
        if result.stderr:
            error_msg += f"\n{result.stderr}"
        raise RuntimeError(error_msg)
    return {line for line in result.stdout.splitlines() if line}


def _filter_mapping_by_modified_files(
    mapping: dict[str, str], modified_files: set[str]
) -> dict[str, str]:
    """Keep only mapping entries where py_file or yaml_file is in modified_files."""
    return {
        py_file: yaml_file
        for py_file, yaml_file in mapping.items()
        if py_file in modified_files or yaml_file in modified_files
    }


def main(argv: list[str] | None = None) -> int:
    """Validate each py→yaml pair from the map file; return 1 on first failure, 0 on success."""
    args = _parse_args(argv)
    map_file = args.map_file
    compile_args_str = args.compile_args
    extra_args = shlex.split(compile_args_str) if compile_args_str else []

    try:
        mapping = _load_and_validate_mapping(map_file)

        if args.modified_only:
            base_branch = _resolve_base_branch()
            modified_files = _get_git_modified_files(base_branch)
            mapping = _filter_mapping_by_modified_files(mapping, modified_files)
            if not mapping:
                print("ℹ️ No modified files match the pipeline map; nothing to validate.")
                return 0
            entry_word = "entry" if len(mapping) == 1 else "entries"
            print(f"ℹ️ Validating {len(mapping)} pipeline map {entry_word} (modified only).")

        with tempfile.TemporaryDirectory() as tmp_dir:
            compiled = Path(tmp_dir) / "tmp.yaml"
            for py_file, yaml_file in mapping.items():
                _check_one(py_file, yaml_file, compiled, extra_args, compile_args_str)

        return 0
    except RuntimeError as e:
        print(e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
