# Deployment
_Last updated: 2026-05-07_

## Distribution Channels

The plugin is distributed via two channels:

| Channel | Audience | Discoverability | Update mechanism |
|---|---|---|---|
| **plugins.qgis.org** | All QGIS users | High — appears in built-in Plugin Manager | Auto-update via Plugin Manager |
| **GitHub Releases** | Power users / preview | Manual (link from docs / sales) | Re-download ZIP, install via "Install from ZIP" |

Cut every release to GitHub first, then promote to plugins.qgis.org after smoke-testing.

## Release Steps

### 1. Bump version

Edit `infrared_city_gis/metadata.txt`:

```ini
version=0.2.2
changelog=0.2.2
    - <bullet 1>
    - <bullet 2>
```

Commit on `main`:

```bash
git commit -am "chore: bump version to 0.2.2"
git push origin main
```

### 2. Tag and push

```bash
git tag v0.2.2
git push --tags
```

This triggers `.github/workflows/release.yml` which:
- Zips `infrared_city_gis/`, excluding caches (`*__pycache__*`, `*.pyc`, `*.pyo`, `*.DS_Store`), all hidden files (`*/.*`), the dev-only test dirs (`infrared_city_gis/tests/*`, `infrared_city_gis/test/*`), and packaging helpers (`plugin_upload.py`, `pb_tool.cfg`, `pylintrc`, `Makefile`) — keeps the uploaded package free of hidden-file warnings on plugins.qgis.org
- Creates a GitHub Release with the ZIP attached and auto-generated release notes

### 3. Upload to plugins.qgis.org (manual, ~2 minutes)

Web UI:
1. Go to https://plugins.qgis.org/plugins/
2. Log in, navigate to **Infrared City GIS** plugin page
3. Click **New version**, upload the ZIP from the GitHub Release

### 4. Promote out of `experimental` (when ready)

Edit `metadata.txt`:
```ini
experimental=False
```

Cut a new patch release. plugins.qgis.org users will start seeing it in default search.

## Environments

There's no staging deployment for the plugin itself — it's a client. Test against Infrared API environments by changing the base URL in the auth dialog (or by editing `constants.py` for development builds):

| Environment | Base URL | Notes |
|---|---|---|
| Production | `https://api.infrared.city/v2` | Real subscription billing |
| Staging | `https://api-test.infrared.city/v2` | Test API keys, no billing |

## Approval Latency

plugins.qgis.org reviews new versions within ~1–3 business days. Initial plugin submission can take longer (≤2 weeks). Plan accordingly when promising release dates.

## Rollback

If a release breaks things:

1. Mark the version as **invalid** on plugins.qgis.org (the Plugin Manager will offer the previous version automatically)
2. Cut a `v0.2.2-fix1` or revert and tag `v0.2.3`
3. Update `experimental=True` if the bug is severe and you need to keep users on the prior stable release

## Auto-Update Behavior

QGIS Plugin Manager checks for updates every time it's opened (subject to user settings). Users running an older version are nudged but not forced. Don't assume everyone is on the latest.
