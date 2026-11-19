# Releasing

For the maintainer.

## One-time setup

1. On PyPI, add a **trusted publisher** for project `agent-doorbell`: owner `mraj602`,
   repository `agent-doorbell`, workflow `release.yml`, environment `pypi`.
2. On GitHub, create the environment `pypi` (Settings → Environments), optionally with a
   required reviewer.
3. Enable GitHub Pages with source **GitHub Actions** (Settings → Pages) for `site/`.
4. Turn on private vulnerability reporting (Settings → Security).

## Every release

1. Set the version in `doorbell/__init__.py`.
2. In `CHANGELOG.md`, rename `[Unreleased]` (or the `unreleased` heading) to
   `[X.Y.Z] - YYYY-MM-DD`.
3. Commit with sign-off on `dev`, open a pull request from `dev` to `main`, and merge it once
   CI is green.
4. Tag and push: `git tag -s vX.Y.Z -m vX.Y.Z && git push origin vX.Y.Z`.
5. `release.yml` checks the tag matches the version, builds the sdist and wheel, creates the
   GitHub Release with the changelog section, and publishes to PyPI.

## Without GitHub Actions

If Actions is unavailable, release by hand:

```bash
python -m pip install build twine
python -m build
twine upload dist/*
gh release create vX.Y.Z dist/* --notes-file <(sed -n '/^## \[X.Y.Z\]/,/^## \[/p' CHANGELOG.md | sed '$d')
```
