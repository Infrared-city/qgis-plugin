# Release Process

Releases are automated via [Release Please](https://github.com/googleapis/release-please).

## How it works

This is a **two-PR process**:

**Step 1 — Merge staging into main**
1. When staging is ready to ship, open a PR from `staging → main` and merge it
2. Release Please detects the new commits on `main` and opens (or updates) a release PR titled `chore(main): release X.Y.Z`
   - This PR bumps the version in `infrared_city_gis/metadata.txt`
   - This PR updates `CHANGELOG.md` based on conventional commit messages
   - Merging `staging → main` alone does **not** create a release

**Step 2 — Merge the Release Please PR**
3. Review and merge the Release Please PR on `main`
4. This triggers the full release pipeline automatically:
   - Release Please creates a `vX.Y.Z` git tag
   - Release Please creates a GitHub Release
   - The `release.yml` workflow triggers on the new tag and builds + attaches the plugin ZIP

**Step 3 — Sync main back to staging**
5. After the release, merge `main → staging` to bring the version bump and changelog back:
   ```bash
   git checkout staging && git merge main && git push origin staging
   ```

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
