# Release Process

Releases are automated via [Release Please](https://github.com/googleapis/release-please).

## How it works

This is a **two-PR process**:

**Step 1 — Merge staging into main**
1. When staging is ready to ship, open a PR from `staging → main` and merge it
   - **The PR title must start with `fix:` or `feat:` if the release contains user-facing changes** — Release Please reads the merge commit message to determine whether to open a release PR. A title starting with `chore:` or `docs:` will not trigger a version bump.
   - Example title: `fix: weather file upload and code quality improvements`
2. Release Please detects the new commits on `main` and opens (or updates) a release PR titled `chore(main): release X.Y.Z`
   - This PR bumps `version.txt` (primary version source for Release Please)
   - This PR updates `CHANGELOG.md` based on conventional commit messages
   - Merging `staging → main` alone does **not** create a release

**Step 1b — Bump `metadata.txt` by hand on the release branch** ⚠️

Release Please does **not** touch `infrared_city_gis/metadata.txt`. The `generic`
extra-file entry in `release-please-config.json` carries a `pattern` key, which
that updater does not support — the updater is annotation-driven, so the entry is
a no-op and the release PR only ever contains three files. **This is a deliberate
manual step, not a bug to fix.** Before merging the release PR, commit to its
branch (`release-please--branches--main--components--infrared-city-qgis`):

- `version=X.Y.Z` — **QGIS packages and distributes the plugin from this field**.
  Miss it and the ZIP ships under the previous version, so nobody receives the update.
- `changelog=X.Y.Z` plus the new entry's bullets — user-facing text, shown in the
  QGIS Plugin Manager. Release Please cannot generate it (it substitutes version
  strings only); keep it free of internal detail.

Verify all three version sources agree before merging:

```bash
python3 -c "
import configparser, json
c = configparser.ConfigParser(); c.read('infrared_city_gis/metadata.txt', encoding='utf-8')
print('metadata.txt', c['general']['version'], '| version.txt', open('version.txt').read().strip(),
      '| manifest', json.load(open('.release-please-manifest.json'))['.'])"
```

> Re-running the Release Please workflow **force-pushes** that branch and wipes the
> manual commit. Do the bump last, and don't trigger the workflow afterwards.

**Step 2 — Merge the Release Please PR**
3. Review and merge the Release Please PR on `main`
4. This triggers the full release pipeline automatically:
   - Release Please creates a `vX.Y.Z` git tag
   - The `release.yml` workflow triggers on the new tag and builds + attaches the plugin ZIP to a GitHub Release

**Step 3 — Sync main back to staging manually**
5. After the release, merge main back into staging:
   ```bash
   git checkout staging && git merge main && git push origin staging
   ```

## Commit message conventions

Release Please reads conventional commits to determine the version bump and generate the changelog:

| Prefix | Version bump | Triggers release PR? | Example |
|--------|-------------|----------------------|---------|
| `fix:` | patch (0.2.4 → 0.2.5) | ✅ yes | `fix: correct bbox calculation` |
| `feat:` | minor (0.2.4 → 0.3.0) | ✅ yes | `feat: add weather file upload` |
| `feat!:` or `BREAKING CHANGE` | major (0.2.4 → 1.0.0) | ✅ yes | `feat!: redesign dialog API` |
| `chore:`, `docs:`, `refactor:` | none | ❌ no | `chore: update dependencies` |

> **Important:** When merging `staging → main`, use a PR title that reflects the highest-impact change in the batch. If the batch contains any `fix:` or `feat:` commits, the PR title should start with `fix:` or `feat:` accordingly.

## Version sources

There are three version sources — all must stay in sync:

| File | Purpose | Updated by |
|------|---------|-----------|
| `version.txt` | Primary version source for Release Please | Release Please |
| `.release-please-manifest.json` | Release Please's own state | Release Please |
| `infrared_city_gis/metadata.txt` (`version=`) | **The version QGIS ships and users see** | **You, by hand** — see Step 1b |

## Manual release (fallback)

If Release Please is unavailable, bump the version manually in both files:

```bash
# 1. Update version.txt and infrared_city_gis/metadata.txt
# 2. Commit and push to main
git tag v0.2.6 && git push --tags
# CI builds the ZIP and creates the GitHub Release automatically
```

## plugins.qgis.org

Upload to plugins.qgis.org is still **manual** — download the ZIP from the GitHub Release and upload via the web UI. See [deployment.md](deployment.md) for details.

## Required secrets

| Secret | Scope | Purpose |
|--------|-------|---------|
| `RELEASE_PLEASE_TOKEN` | Contents R/W, Pull requests R/W | Allows Release Please to open PRs and create tags |
