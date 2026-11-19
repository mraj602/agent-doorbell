# Contributing to Agent Doorbell

Thanks for helping. Small, focused pull requests are easiest to review. Please read the
[Code of Conduct](CODE_OF_CONDUCT.md) first.

## Development setup

You need Python 3.10 or newer.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
```

## Branches

- `main` is what was last released. It is protected: changes arrive only through pull
  requests that pass CI and the DCO check, and history stays linear (squash or rebase merges).
- `dev` collects work for the next release. Open feature pull requests against `dev`; a
  release is a pull request from `dev` to `main`, followed by a tag (see
  [docs/RELEASING.md](docs/RELEASING.md)).
- Fixes for the released version can go straight to `main` and are merged back into `dev`.

## Tests

- `pytest` runs the offline tests in `tests/`. They use a fake Slack and need no network or
  token. They must pass before a pull request is merged, and CI runs them on Linux and
  macOS (Windows runs too, without blocking, while its support is new).
- `tests/e2e_slack.py` is an optional end-to-end check against a real Slack workspace. It is
  driven by environment variables (see the top of the file), is not run by CI, and should only
  be used with a throwaway workspace and token. Never commit a token, a message or a workspace
  name.

A bug fix should come with a test that fails without it. A new feature should come with tests.

## Style

- Keep dependencies to the Python standard library plus PyYAML. Do not add another dependency
  without discussing it in an issue first.
- Keep the daemon free of LLM calls, and don't make it write agent harness settings (see [SECURITY.md](SECURITY.md)).
- Keep changes small and focused; do unrelated cleanups in a separate pull request.
- Update `CHANGELOG.md` (under `[Unreleased]`) and the docs for user-facing changes.

## Developer Certificate of Origin

There is no CLA. Instead, every commit must be signed off to certify the
[Developer Certificate of Origin](https://developercertificate.org/):

```sh
git commit -s -m "Fix the thing"
```

This adds a `Signed-off-by: Your Name <you@example.com>` line that matches the commit author.
To sign off commits you already made: `git rebase --signoff main`, then
`git push --force-with-lease`. A CI check fails pull requests with unsigned commits.

## Reporting bugs

Open an issue using the bug report template. Include the output of `doorbell --version` and
`doorbell doctor`, your OS, and the steps to reproduce. Remove tokens, message text, channel
names and other personal data first. For security problems, do not open an issue; follow
[SECURITY.md](SECURITY.md).
