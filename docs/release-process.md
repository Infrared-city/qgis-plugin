# Release Process

Releases are automated via [Release Please](https://github.com/googleapis/release-please).

## How it works

1. Developers merge PRs into **staging** (integration branch)
2. When staging is ready, open a PR from **staging → main** and merge it
3. Release Please detects the new commits on `main` and opens (or updates) a release PR titled `chore(main): release X.Y.Z`
4. The release PR contains:
   - Bumped version in `infrared_city_gis/metadata.txt`
   - Updated `CHANGELOG.md` based on conventional commit messages
5. Review and merge the release PR
6. Release Please automatically:
   - Creates a `vX.Y.Z` git tag
   - Creates a GitHub Release
7. The existing `release.yml` workflow triggers on the new tag and builds + attaches the plugin ZIP to the GitHub Release

## Commit message conventions

Release Please reads conventional commits to determine the version bump and generate the changelog:

| Prefix | Version bump | Example |
|--------|-------------|---------|
| `fix:` | patch (0.2.4 → 0.2.5) | `fix: correct bbox calculation` |
| `feat:` | minor (0.2.4 → 0.3.0) | `feat: add weather file upload` |
| `feat!:` or `BREAKING CHANGE` | major (0.2.4 → 1.0.0) | `feat!: redesign dialog API` |
| `chore:`, `docs:`, `refactor:` | no bump (changelog only) | `chore: update dependencies` |

## Manual release (fallback)

If Release Please is unavailable, bump the version manually:

```bash
# 1. Update version in metadata.txt
# 2. Commit and push to main
git tag v0.2.5 && git push --tags
# CI builds the ZIP and creates the GitHub Release automatically
```

## plugins.qgis.org

Upload to plugins.qgis.org is still **manual** — download the ZIP from the GitHub Release and upload via the web UI. See [deployment.md](deployment.md) for details.

## Required secrets

| Secret | Scope | Purpose |
|--------|-------|---------|
| `RELEASE_PLEASE_TOKEN` | Contents R/W, Pull requests R/W | Allows Release Please to open PRs and create tags |
