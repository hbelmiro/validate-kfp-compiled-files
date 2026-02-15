# Agents

## Code style

- Never disable lint rules.
- Always format Markdown tables.

## Verification

- Run all checks before considering work done:
  - `uv run pytest -v`
  - `uv run pylint .`
  - `uv run ty check`
  - `npx markdownlint-cli2 '**/*.md'`

## Architecture

- Keep `action.yaml` minimal — all argument handling
  and logic must live in Python, not bash.
- New CLI arguments go through `argparse` in
  `_parse_args()`. Tests pass `argv` lists to
  `main(argv)` directly instead of monkeypatching
  `sys.argv`.

## Assumptions

- `git` is always available at runtime (GitHub Actions
  runners ship it). Do not write tests for
  "git not installed" scenarios.
- Users must not be asked to configure
  `fetch-depth` or any other checkout setting.
  The action is responsible for fetching whatever
  history it needs.
