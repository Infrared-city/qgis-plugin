# Battle Scars
_Non-obvious bugs, hard-won lessons. Date each entry. Remove when root cause is fixed._

(none yet — log gotchas as you hit them)

## Format

- **[YYYY-MM-DD]** [area] Short description — why it happens and how to work around it.

## Common QGIS Plugin Pitfalls (general — not yet hit here)

- **Don't `import requests` at module top-level if you might run inside QGIS server**: server profiles can have stripped Python. Wrap in a function-level import or guard with try/except.
- **`pb_tool zip` excludes files based on `.gitignore` only loosely**: always verify the ZIP contents before uploading to plugins.qgis.org.
- **`metadata.txt` `version=` must be SemVer** — plugins.qgis.org rejects non-semver tags.
- **`experimental=True`** keeps the plugin out of default search results until you flip it. Useful while iterating, but remember to flip when ready.
- **Plugin reload in QGIS doesn't always pick up `__init__.py` changes.** Use the *Plugin Reloader* community plugin or fully restart QGIS.
