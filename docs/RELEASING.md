# Releasing

For the maintainer. Agent Doorbell is published in three places, and a release updates all
of them:

| Where | What | How users get it |
|---|---|---|
| [PyPI](https://pypi.org/project/agent-doorbell/) | sdist and wheel | `pipx install agent-doorbell` |
| [GitHub Releases](https://github.com/mraj602/agent-doorbell/releases) | the same files, plus the changelog section | download, or `pipx install git+https://github.com/mraj602/agent-doorbell@vX.Y.Z` |
| This repository's plugin marketplace | the Claude Code plugin in `plugin/agent-doorbell/` | `/plugin marketplace add mraj602/agent-doorbell`; updates arrive when `main` changes |

## Branches and protection

`main` is the released code. `dev` collects work for the next release. Rulesets on `main`
require a pull request, linear history (squash or rebase merges) and the CI and `dco` checks;
`main`, `dev` and `v*` tags can't be deleted or force-pushed. Repository admins can bypass
them, which is only for emergencies and for the time Actions is unavailable.

## One-time setup for automatic releases

1. On PyPI, open the project → **Manage → Publishing → Add a new publisher → GitHub**: owner
   `mraj602`, repository `agent-doorbell`, workflow `release.yml`, environment `pypi`.
2. On GitHub, create the environment `pypi` (Settings → Environments), optionally with
   yourself as required reviewer.
3. Enable GitHub Pages with source **GitHub Actions** (Settings → Pages) for `site/`.

## Every release

1. On `dev`, set the version in **both** `doorbell/__init__.py` and
   `plugin/agent-doorbell/.claude-plugin/plugin.json` (a test checks they match).
2. In `CHANGELOG.md`, move the `[Unreleased]` entries under `## [X.Y.Z] - YYYY-MM-DD`, keep an
   empty `[Unreleased]` heading, and update the links at the bottom.
3. Open a pull request from `dev` to `main` and merge it once CI is green.
4. Tag `main` and push the tag: `git tag -a vX.Y.Z -m vX.Y.Z && git push origin vX.Y.Z`.
5. `release.yml` checks the tag matches the version, builds the sdist and wheel, creates the
   GitHub Release with the changelog section, and publishes to PyPI.
6. Check: `pipx install agent-doorbell==X.Y.Z` in a clean environment, and
   `claude plugin marketplace update agent-doorbell` shows the new plugin version.

## Without GitHub Actions

If Actions is unavailable, do steps 1 to 4, then release by hand. Use a PyPI API token scoped
to the `agent-doorbell` project, and never put it on the command line:

```bash
python -m pip install build twine
rm -rf dist && python -m build
twine check dist/*
sed -n '/^## \[X.Y.Z\]/,/^## \[/{/^## \[/!p}' CHANGELOG.md > /tmp/notes.md
gh release create vX.Y.Z dist/* --title "Agent Doorbell for Slack X.Y.Z" --notes-file /tmp/notes.md
TWINE_USERNAME=__token__ twine upload dist/*     # asks for the token
```

PyPI files are permanent: a version can't be uploaded twice, so check the files first.
