"""Tests for verify_kfp_compiled.py."""

import io
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import verify_kfp_compiled

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "verify_kfp_compiled.py"


@dataclass
class RunConfig:
    """Configuration for _run_main function."""
    extra_args: str = ""
    monkeypatch: pytest.MonkeyPatch | None = None
    mock_subprocess_run: MagicMock | None = None
    modified_only: bool = False


def run_script(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run verify_kfp_compiled.py with the given args."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


# ----- Subprocess tests (no mocks) -----


def test_missing_map_file_exits_nonzero(tmp_path: Path) -> None:
    """When the pipeline map file does not exist, script exits with 1."""
    result = run_script("--map-file", str(tmp_path / "nonexistent.json"))
    assert result.returncode == 1
    assert "Pipeline map file not found" in result.stdout
    assert "nonexistent.json" in result.stdout


def test_invalid_json_exits_nonzero(tmp_path: Path) -> None:
    """When the map file contains invalid JSON, script exits with 1."""
    map_file = tmp_path / "map.json"
    map_file.write_text("{ invalid }")
    result = run_script("--map-file", str(map_file))
    assert result.returncode == 1
    assert "Invalid JSON" in result.stdout
    assert "map.json" in result.stdout


def test_map_file_must_be_json_object(tmp_path: Path) -> None:
    """When the map file is valid JSON but not an object, script exits with 1."""
    map_file = tmp_path / "map.json"
    map_file.write_text("[1, 2, 3]")
    result = run_script("--map-file", str(map_file))
    assert result.returncode == 1
    assert "must be a JSON object" in result.stdout


def test_map_entries_must_be_strings(tmp_path: Path) -> None:
    """When the map has non-string key or value, script exits with 1."""
    map_file = tmp_path / "map.json"
    map_file.write_text('{"p.py": 123}')
    result = run_script("--map-file", str(map_file), cwd=tmp_path)
    assert result.returncode == 1
    assert "must be strings" in result.stdout or "non-string" in result.stdout


def test_empty_map_exits_zero(tmp_path: Path) -> None:
    """When the map is an empty object, script exits with 0."""
    map_file = tmp_path / "map.json"
    map_file.write_text("{}")
    result = run_script("--map-file", str(map_file))
    assert result.returncode == 0


def test_missing_py_file_exits_nonzero(tmp_path: Path) -> None:
    """When a mapped .py file does not exist, script exits with 1."""
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps({"missing.py": "out.yaml"}))
    (tmp_path / "out.yaml").write_text("existing: yaml\n")
    result = run_script("--map-file", str(map_file), cwd=tmp_path)
    assert result.returncode == 1
    assert "Python file not found" in result.stdout
    assert "missing.py" in result.stdout


def test_missing_yaml_file_exits_nonzero(tmp_path: Path) -> None:
    """When a mapped .yaml file does not exist, script exits with 1."""
    map_file = tmp_path / "map.json"
    (tmp_path / "real.py").write_text("# dummy pipeline\n")
    map_file.write_text(json.dumps({"real.py": "missing.yaml"}))
    result = run_script("--map-file", str(map_file), cwd=tmp_path)
    assert result.returncode == 1
    assert "Expected YAML missing" in result.stdout
    assert "missing.yaml" in result.stdout


def test_explicit_nonexistent_map_path_exits_nonzero(tmp_path: Path) -> None:
    """With --map-file pointing to nonexistent path, script exits 1."""
    result = run_script("--map-file", str(tmp_path / "no-such-map.json"))
    assert result.returncode == 1
    assert "no-such-map.json" in result.stdout


def test_default_map_path_when_no_args(tmp_path: Path) -> None:
    """With no args, script looks for .github/kfp-pipelines-map.json in cwd."""
    # No map at default path in tmp_path
    result = run_script(cwd=tmp_path)
    assert result.returncode == 1
    assert ".github/kfp-pipelines-map.json" in result.stdout


# ----- In-process tests with mocked subprocess -----


def _make_pipeline_map(
    tmp_path: Path,
    *,
    py_content: str = "# dummy\n",
    yaml_content: str = "a: 1\n",
) -> Path:
    """Create p.py, p.yaml, and map.json in tmp_path; return path to map.json."""
    (tmp_path / "p.py").write_text(py_content)
    (tmp_path / "p.yaml").write_text(yaml_content)
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps({"p.py": "p.yaml"}))
    return map_file


def _fake_kfp_run(compiled_content: str):
    """Return a callable that mimics kfp writing compiled_content to --output path."""

    def _run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        out_idx = cmd.index("--output")
        out_path = Path(cmd[out_idx + 1])
        out_path.write_text(compiled_content, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    return _run


def _run_main(map_path: str | None = None, config: RunConfig | None = None) -> tuple[int, str]:
    """Run main() with given argv; return (exit_code, stdout). map_path=None => no args."""
    if config is None:
        config = RunConfig()

    argv: list[str] = []
    if map_path is not None:
        argv.extend(["--map-file", map_path])
    if config.extra_args:
        argv.append(f"--compile-args={config.extra_args}")
    if config.modified_only:
        argv.append("--modified-only")

    stdout_capture = io.StringIO()
    if config.monkeypatch is not None:
        config.monkeypatch.setattr(sys, "stdout", stdout_capture)

    patch_target = "verify_kfp_compiled.subprocess.run"
    if config.mock_subprocess_run is not None:
        with patch(patch_target, config.mock_subprocess_run):
            code = verify_kfp_compiled.main(argv)
    else:
        code = verify_kfp_compiled.main(argv)

    return code, stdout_capture.getvalue()


def test_map_not_found_in_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-process: nonexistent map path exits 1 and prints message."""
    monkeypatch.chdir(tmp_path)
    code, out = _run_main("no-such-map.json", RunConfig(monkeypatch=monkeypatch))
    assert code == 1
    assert "Pipeline map file not found" in out
    assert "no-such-map.json" in out


def test_invalid_json_in_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-process: invalid JSON in map file exits 1."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "map.json").write_text("{ invalid }")
    code, out = _run_main("map.json", RunConfig(monkeypatch=monkeypatch))
    assert code == 1
    assert "Invalid JSON" in out


def test_default_argv_in_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-process: no args uses default map path and exits 1 when not found."""
    monkeypatch.chdir(tmp_path)
    code, out = _run_main(map_path=None, config=RunConfig(monkeypatch=monkeypatch))
    assert code == 1
    assert ".github/kfp-pipelines-map.json" in out or "not found" in out


def test_missing_py_file_in_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-process: mapped .py file missing exits 1 before calling kfp."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "out.yaml").write_text("x: 1\n")
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps({"missing.py": "out.yaml"}))
    code, out = _run_main("map.json", RunConfig(monkeypatch=monkeypatch))
    assert code == 1
    assert "Python file not found" in out
    assert "missing.py" in out


def test_missing_yaml_file_in_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-process: mapped .yaml file missing exits 1 before calling kfp."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "real.py").write_text("# dummy\n")
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps({"real.py": "missing.yaml"}))
    code, out = _run_main("map.json", RunConfig(monkeypatch=monkeypatch))
    assert code == 1
    assert "Expected YAML missing" in out
    assert "missing.yaml" in out


def test_kfp_compile_failure_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When kfp dsl compile fails, script exits 1 and prints stderr."""
    monkeypatch.chdir(tmp_path)
    _make_pipeline_map(tmp_path)

    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            ["kfp", "dsl", "compile"],
            returncode=1,
            stdout="",
            stderr="kfp compile error",
        )
    )

    code, out = _run_main(
        "map.json",
        RunConfig(monkeypatch=monkeypatch, mock_subprocess_run=mock_run),
    )
    assert code == 1
    assert "kfp dsl compile failed" in out
    assert "kfp compile error" in out


def test_yaml_out_of_date_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When compiled YAML differs from saved, script exits 1 with diff and fix command."""
    monkeypatch.chdir(tmp_path)
    _make_pipeline_map(tmp_path, yaml_content="old: content\n")

    code, out = _run_main(
        "map.json",
        RunConfig(
            monkeypatch=monkeypatch,
            mock_subprocess_run=MagicMock(side_effect=_fake_kfp_run("new: content\n")),
        ),
    )
    assert code == 1
    assert "out of date" in out
    assert ("old:" in out and "new:" in out) or "---" in out
    assert "kfp dsl compile" in out
    assert "p.py" in out and "p.yaml" in out


def test_yaml_out_of_date_includes_extra_args_in_fix_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix command in output includes extra compile args when provided."""
    monkeypatch.chdir(tmp_path)
    _make_pipeline_map(tmp_path)

    code, out = _run_main(
        "map.json",
        RunConfig(
            extra_args="--pipeline-root gs://bucket",
            monkeypatch=monkeypatch,
            mock_subprocess_run=MagicMock(side_effect=_fake_kfp_run("b: 2\n")),
        ),
    )
    assert code == 1
    assert "--pipeline-root gs://bucket" in out or "gs://bucket" in out


def test_yaml_up_to_date_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When compiled YAML matches saved, script exits 0 and prints up to date."""
    monkeypatch.chdir(tmp_path)
    content = "same: content\n"
    _make_pipeline_map(tmp_path, yaml_content=content)

    code, out = _run_main(
        "map.json",
        RunConfig(
            monkeypatch=monkeypatch,
            mock_subprocess_run=MagicMock(side_effect=_fake_kfp_run(content)),
        ),
    )
    assert code == 0
    assert "up to date" in out


def test_extra_args_passed_to_kfp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extra compile args are passed to the kfp subprocess call."""
    monkeypatch.chdir(tmp_path)
    content = "a: 1\n"
    _make_pipeline_map(tmp_path, yaml_content=content)

    mock_run = MagicMock(side_effect=_fake_kfp_run(content))
    code, _ = _run_main(
        "map.json",
        RunConfig(
            extra_args="--pipeline-root s3://bucket",
            monkeypatch=monkeypatch,
            mock_subprocess_run=mock_run,
        ),
    )
    assert code == 0
    call_args = mock_run.call_args[0][0]
    assert "--pipeline-root" in call_args
    assert "s3://bucket" in call_args


def test_compile_args_starting_with_dashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--compile-args accepts values that start with dashes (e.g. --some-flag)."""
    monkeypatch.chdir(tmp_path)
    content = "a: 1\n"
    _make_pipeline_map(tmp_path, yaml_content=content)

    mock_run = MagicMock(side_effect=_fake_kfp_run(content))
    code, _ = _run_main(
        "map.json",
        RunConfig(
            extra_args="--disable-execution-caching-by-default",
            monkeypatch=monkeypatch,
            mock_subprocess_run=mock_run,
        ),
    )
    assert code == 0
    call_args = mock_run.call_args[0][0]
    assert "--disable-execution-caching-by-default" in call_args


def test_compile_args_with_dashes_via_equals_syntax(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--compile-args=<value> works even when value starts with dashes."""
    monkeypatch.chdir(tmp_path)
    content = "a: 1\n"
    _make_pipeline_map(tmp_path, yaml_content=content)

    mock_run = MagicMock(side_effect=_fake_kfp_run(content))

    stdout_capture = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_capture)

    with patch("verify_kfp_compiled.subprocess.run", mock_run):
        code = verify_kfp_compiled.main([
            "--map-file", "map.json",
            "--compile-args=--disable-execution-caching-by-default",
        ])
    assert code == 0
    call_args = mock_run.call_args[0][0]
    assert "--disable-execution-caching-by-default" in call_args


# ----- Git-modified-only tests -----


def _make_dual_mock(
    git_diff_output: str,
    compiled_content: str,
    *,
    git_diff: tuple[int, str] = (0, ""),
    git_fetch: tuple[int, str] = (0, ""),
):
    """Return a MagicMock that dispatches to git, kfp based on cmd."""

    def _dispatch(
        cmd: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess:
        if len(cmd) >= 2 and cmd[0] == "git":
            if cmd[1] == "fetch":
                return subprocess.CompletedProcess(
                    cmd,
                    returncode=git_fetch[0],
                    stdout="",
                    stderr=git_fetch[1],
                )
            if cmd[1] == "diff":
                return subprocess.CompletedProcess(
                    cmd,
                    returncode=git_diff[0],
                    stdout=git_diff_output,
                    stderr=git_diff[1],
                )
        # kfp compile
        try:
            out_idx = cmd.index("--output")
        except ValueError as exc:
            msg = f"Unexpected command (no --output flag): {cmd}"
            raise AssertionError(msg) from exc
        if out_idx + 1 >= len(cmd):
            msg = f"--output flag missing its value: {cmd}"
            raise AssertionError(msg)
        out_path = Path(cmd[out_idx + 1])
        out_path.write_text(compiled_content, encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout="", stderr=""
        )

    return MagicMock(side_effect=_dispatch)


def test_modified_only_filters_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With --modified-only, only entries whose files appear in git diff are validated."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    content = "a: 1\n"
    (tmp_path / "p1.py").write_text("# pipeline 1\n")
    (tmp_path / "p1.yaml").write_text(content)
    (tmp_path / "p2.py").write_text("# pipeline 2\n")
    (tmp_path / "p2.yaml").write_text(content)
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps({"p1.py": "p1.yaml", "p2.py": "p2.yaml"}))

    mock_run = _make_dual_mock(git_diff_output="p1.py\n", compiled_content=content)

    code, out = _run_main(
        "map.json",
        RunConfig(
            monkeypatch=monkeypatch,
            mock_subprocess_run=mock_run,
            modified_only=True,
        ),
    )
    assert code == 0
    assert "p1.py" in out
    assert "p2.py" not in out
    assert "Validating 1 pipeline map entry" in out


def test_modified_only_no_matches_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With --modified-only and no modified files matching the map, exit 0."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    (tmp_path / "p.py").write_text("# dummy\n")
    (tmp_path / "p.yaml").write_text("a: 1\n")
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps({"p.py": "p.yaml"}))

    mock_run = _make_dual_mock(
        git_diff_output="unrelated_file.txt\n", compiled_content="a: 1\n"
    )

    code, out = _run_main(
        "map.json",
        RunConfig(
            monkeypatch=monkeypatch,
            mock_subprocess_run=mock_run,
            modified_only=True,
        ),
    )
    assert code == 0
    assert "nothing to validate" in out


def test_modified_only_matches_yaml_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With --modified-only, modifying the .yaml file also triggers validation."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    content = "a: 1\n"
    (tmp_path / "p.py").write_text("# dummy\n")
    (tmp_path / "p.yaml").write_text(content)
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps({"p.py": "p.yaml"}))

    mock_run = _make_dual_mock(git_diff_output="p.yaml\n", compiled_content=content)

    code, out = _run_main(
        "map.json",
        RunConfig(
            monkeypatch=monkeypatch,
            mock_subprocess_run=mock_run,
            modified_only=True,
        ),
    )
    assert code == 0
    assert "up to date" in out


def test_modified_only_reads_github_base_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GITHUB_BASE_REF is normalised to origin/<branch> for fetch and merge-base diff."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    content = "a: 1\n"
    _make_pipeline_map(tmp_path, yaml_content=content)

    mock_run = _make_dual_mock(git_diff_output="p.py\n", compiled_content=content)

    code, _ = _run_main(
        "map.json",
        RunConfig(
            monkeypatch=monkeypatch,
            mock_subprocess_run=mock_run,
            modified_only=True,
        ),
    )
    assert code == 0
    # First git call is fetch, second is diff
    fetch_cmd = mock_run.call_args_list[0][0][0]
    assert fetch_cmd == ["git", "fetch", "origin", "main"]
    diff_cmd = mock_run.call_args_list[1][0][0]
    assert "origin/main...HEAD" in diff_cmd


def test_modified_only_without_github_base_ref_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without GITHUB_BASE_REF, --modified-only exits 1 with a clear error."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    _make_pipeline_map(tmp_path)

    code, out = _run_main(
        "map.json",
        RunConfig(
            monkeypatch=monkeypatch,
            modified_only=True,
        ),
    )
    assert code == 1
    assert "GITHUB_BASE_REF" in out
    assert "pull_request" in out


def test_git_diff_failure_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When git diff returns non-zero, exit 1 with error message."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    _make_pipeline_map(tmp_path)

    mock_run = _make_dual_mock(
        git_diff_output="",
        compiled_content="",
        git_diff=(128, "fatal: bad revision"),
    )

    code, out = _run_main(
        "map.json",
        RunConfig(
            monkeypatch=monkeypatch,
            mock_subprocess_run=mock_run,
            modified_only=True,
        ),
    )
    assert code == 1
    assert "git diff failed" in out


def test_git_fetch_failure_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When git fetch fails, exit 1 with error message."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    _make_pipeline_map(tmp_path)

    mock_run = _make_dual_mock(
        git_diff_output="",
        compiled_content="",
        git_fetch=(128, "fatal: could not fetch"),
    )

    code, out = _run_main(
        "map.json",
        RunConfig(
            monkeypatch=monkeypatch,
            mock_subprocess_run=mock_run,
            modified_only=True,
        ),
    )
    assert code == 1
    assert "git fetch failed" in out


# ----- Optional integration test (real kfp via uv workspace) -----


def _uv_run_integration(
    tmp_path: Path, *cmd: str
) -> subprocess.CompletedProcess:
    """Run a command with uv run --package integration-env and cwd=tmp_path.

    Uses --project so uv discovers the workspace from repo root while
    --directory makes the child process run with cwd=tmp_path.
    """
    return subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(REPO_ROOT),
            "--package",
            "integration-env",
            "--directory",
            str(tmp_path),
            "--",
            *cmd,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )


@pytest.mark.integration
def test_real_kfp_compile_up_to_date(tmp_path: Path) -> None:
    """With kfp from workspace integration-env, compile and verify pass."""
    # Minimal pipeline with one task (kfp v2 requires at least one task)
    pipeline_py = tmp_path / "minimal_pipeline.py"
    pipeline_py.write_text(
        "from kfp import dsl\n"
        "\n"
        "@dsl.component\n"
        "def hello() -> str:\n"
        '    return "hi"\n'
        "\n"
        "@dsl.pipeline(name='minimal')\n"
        "def minimal_pipeline() -> str:\n"
        "    return hello().output\n"
    )
    out_yaml = tmp_path / "minimal_pipeline.yaml"
    result = _uv_run_integration(
        tmp_path,
        "kfp",
        "dsl",
        "compile",
        "--py",
        str(pipeline_py),
        "--output",
        str(out_yaml),
    )
    if result.returncode != 0:
        pytest.skip(f"kfp compile failed: {result.stderr}")
    assert out_yaml.is_file()

    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps({pipeline_py.name: out_yaml.name}))

    run_result = _uv_run_integration(
        tmp_path, "python", str(SCRIPT), "--map-file", "map.json"
    )
    assert run_result.returncode == 0
    assert "up to date" in run_result.stdout
