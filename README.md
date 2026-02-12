# ✅ Validate KFP Compiled Files

A GitHub Action to validate that your
[Kubeflow Pipelines](https://www.kubeflow.org/docs/components/pipelines/) Python
scripts (`.py`) are correctly compiled into their corresponding YAML files
(`.yaml`).

This helps ensure consistency in CI pipelines by preventing out-of-date
compiled pipeline definitions.

---

## 🔧 How It Works

For each entry in a JSON mapping file (`.py` ➝ `.yaml`), this action:

1. Validates the existence of both files.
2. Compiles the Python file using `kfp dsl compile`.
3. Diffs the compiled output with the existing YAML.
4. Fails the workflow if differences are found.

The action is implemented in Python (no `jq` or other system dependencies).
It uses **pip** and the runner’s **Python** to install your `requirements-file`
and run the verifier—**you do not need uv** or any other tool in your repo.

---

## 📦 Inputs

| Name                 | Description                             | Required   |
|----------------------|-----------------------------------------|------------|
| `pipelines-map-file` | Path to JSON mapping `.py` ➝ `.yaml`    | Yes        |
| `requirements-file`  | Path to `requirements.txt` for `kfp`    | Yes        |
| `extra-compile-args` | Extra args for `kfp dsl compile` (opt.) | No         |

---

## 📄 JSON Mapping Format

```json
{
  "pipelines/sample_pipeline.py": "pipelines/sample_pipeline.yaml",
  "other/example.py": "other/example.yaml"
}
```

---

## 🚀 Usage

```yaml
jobs:
  validate-kfp:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout source code
        uses: actions/checkout@v4

      - name: Validate compiled pipelines
        uses: hbelmiro/validate-kfp-compiled-files@<commit-sha>
        with:
          pipelines-map-file: './.github/pipelines-map.json'
          requirements-file: './pipeline/requirements.txt'
          extra-compile-args: '--some-kfp-flag value'
```

---

## 🛠 Development

This repo uses [uv](https://docs.astral.sh/uv/) for Python tooling.

- Install dependencies (including dev): `uv sync --all-groups`
- Run tests: `uv run pytest -v`
- Test coverage: `uv run pytest --cov --cov-report=term-missing -m "not integration"`
- Lint: `uv run pylint .`
- Type check: `uv run ty check`
- Check lock file: `uv lock --check`
- Optional integration test (workspace `tests/integration`, pinned kfp):
  `uv run pytest -v -m integration`
