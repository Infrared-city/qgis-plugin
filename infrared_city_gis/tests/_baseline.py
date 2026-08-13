"""Baseline recording and the end-of-run summary table.

Kept out of ``conftest.py`` so test modules can import these helpers directly —
pytest does not put ``conftest`` itself on the import path. ``conftest`` adds
this directory to ``sys.path`` before collection.

Why baselines rather than fixed expectations: building counts and material
feature counts come from live platform data, and simulation values from a
versioned model. Both are refined over time, so the harness records what the
system produces and reports the drift, failing only past ``DRIFT_LIMIT``.
"""

import json
import os
from pathlib import Path

BASELINE_DIR = Path(__file__).parent / "baselines"

#: Collected rows for the end-of-run table: (step, status, detail).
_SUMMARY: list = []
#: Baseline drift threshold. Beyond this a difference is treated as a failure.
DRIFT_LIMIT = 0.05


def record(step: str, status: str, detail: str) -> None:
    """Add a line to the summary table printed after the run."""
    _SUMMARY.append((step, status, detail))


def baseline_path(name: str) -> Path:
    return BASELINE_DIR / f"{name}.json"


def load_baseline(name: str) -> dict:
    path = baseline_path(name)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_baseline(name: str, data: dict) -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path(name).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def updating_baseline() -> bool:
    return os.environ.get("UPDATE_BASELINE", "").strip() == "1"


def _numeric_detail(value, expected) -> str:
    """One comparison, rendered the same way whether it passed or failed.

    Always carries the direction: "+9.6%" for a count that dropped reads as
    growth and sends you looking the wrong way. Falls back to the bare delta
    when the baseline is zero and a percentage would be meaningless.
    """
    delta = value - expected
    if expected:
        return (f"{value!r} (baseline {expected!r}, Δ {delta:+g} / "
                f"{delta / abs(expected):+.2%})")
    return f"{value!r} (baseline {expected!r}, Δ {delta:+g})"


def compare_to_baseline(name: str, key: str, value, *, tolerant: bool = True,
                        abs_tol: float = 0.0, rel_tol: float = None):
    """Compare ``value`` against the recorded baseline and report the drift.

    Returns the drift as a fraction (0.0 when equal or newly recorded). Raises
    AssertionError when a numeric value moved by more than :data:`DRIFT_LIMIT`.
    Non-numeric values (lists, strings) must match exactly.

    ``abs_tol`` covers the case relative drift cannot express: a baseline of
    exactly 0.0, which several analyses produce legitimately (sky-view factor,
    direct sun hours and solar radiation all bottom out at 0 in deep shade).
    Without it any movement off zero is an automatic failure, because there is
    nothing to divide by. Pass the smallest change that would actually matter
    for that quantity.

    ``rel_tol`` overrides :data:`DRIFT_LIMIT` for one quantity that is known to
    move faster than the rest. Use it with a comment saying why — a wide
    tolerance without a reason is just a disabled assertion.
    """
    limit = DRIFT_LIMIT if rel_tol is None else rel_tol
    data = load_baseline(name)

    if updating_baseline() or key not in data:
        data[key] = value
        save_baseline(name, data)
        record(key, "recorded", f"{value!r}")
        return 0.0

    expected = data[key]
    numeric = isinstance(value, (int, float)) and isinstance(expected, (int, float))

    if expected == value:
        # Spell out the +0.00% even on a match: the point of the comparison is
        # that this run reproduced the recorded one, and a bare value does not
        # say whether it was compared at all.
        record(key, "ok", _numeric_detail(value, expected) if numeric else f"{value!r}")
        return 0.0

    if numeric and abs_tol and abs(value - expected) <= abs_tol:
        record(key, "drift", f"{_numeric_detail(value, expected)} within ±{abs_tol}")
        return 0.0

    if numeric and expected:
        drift = abs(value - expected) / abs(expected)
        detail = _numeric_detail(value, expected)
        if tolerant and drift <= limit:
            record(key, "drift", detail)
            return drift
        record(key, "FAIL", detail)
        raise AssertionError(
            f"{key}: {value!r} vs baseline {expected!r} ({drift:.1%} > "
            f"{limit:.0%}).\n"
            "The platform data may legitimately have been refined, or the fetch "
            "regressed. Verify, then re-record with:\n"
            "    UPDATE_BASELINE=1 pytest -m e2e"
        )

    record(key, "FAIL", f"{value!r} (baseline {expected!r})")
    raise AssertionError(
        f"{key} changed: {value!r} vs baseline {expected!r}.\n"
        "Verify, then re-record with: UPDATE_BASELINE=1 pytest -m e2e"
    )
