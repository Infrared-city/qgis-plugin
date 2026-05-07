from PyQt5.QtGui import QColor
from qgis.core import QgsColorRampShader

from ..infrared_logger import logger
from ..services.fetch_from_registry import (
    fetch_registry_visual_configs,
    load_registry_visual_configs,
)


def _rgb_to_hex(rgb_list):
    """Convert [R, G, B] list to hex string."""
    if not isinstance(rgb_list, (list, tuple)) or len(rgb_list) != 3:
        return None
    try:
        return "#{:02X}{:02X}{:02X}".format(*[int(v) for v in rgb_list])
    except Exception:
        return None


def _extract_config(visual_configs, analysis_type, sub_analysis_type=None):
    """Pick the right config for (analysis_type, sub_analysis_type) and
    normalize colors to hex strings. Returns ``None`` if the analysis
    type / subtype is not present in ``visual_configs``.
    """
    config = visual_configs.get(analysis_type)
    if not config:
        return None

    if sub_analysis_type:
        sub_cfg = config.get(sub_analysis_type)
        if not sub_cfg:
            logger.warning(
                "Subtype '%s' not found under '%s'.", sub_analysis_type, analysis_type
            )
            return None
        cfg = sub_cfg
    else:
        cfg = config

    colors_raw = cfg.get("colors", [])
    colors = [_rgb_to_hex(c) for c in colors_raw if _rgb_to_hex(c)]

    return {
        "colors": colors,
        "steps": cfg.get("steps", []),
        "stepsNames": cfg.get("stepsNames", []),
        "info": cfg.get("info"),
        "unit": cfg.get("unit"),
        "colorInterpolation": cfg.get("colorInterpolation"),
        "legendType": cfg.get("legendType"),
    }


def get_visual_config(analysis_type, sub_analysis_type=None):
    """Return visual configuration (colors, steps, stepsNames, ...) for an
    analysis type.

    Source of truth is ``settings/model_registry.json``. If the file does not
    exist yet, triggers a fetch from the production registry
    (``GET /v2/utils/registry/models``) which persists the response to disk.
    Returns ``None`` if the registry is unavailable or the analysis type is
    not present.
    """
    registry_configs = load_registry_visual_configs()
    if registry_configs is None:
        logger.info("model_registry.json not found on disk — fetching from API")
        registry_configs = fetch_registry_visual_configs()

    if not registry_configs:
        logger.warning("No registry visual configs available")
        return None

    cfg = _extract_config(registry_configs, analysis_type, sub_analysis_type)
    if cfg is not None:
        logger.info(
            "Visual config for '%s%s' loaded from registry",
            analysis_type,
            f"/{sub_analysis_type}" if sub_analysis_type else "",
        )
        return cfg

    logger.warning(
        "Analysis type '%s' not found in registry.",
        analysis_type,
    )
    return None


def _build_color_ramp_items(visual_config, analysis_type, vmin=None, vmax=None):
    colors = visual_config.get("colors", [])
    steps = visual_config.get("steps", [])
    steps_names = visual_config.get("stepsNames", [])
    interpolation = visual_config.get("colorInterpolation", "linear")

    shader = QgsColorRampShader()
    color_items = []

    # ---- categorical values ----
    # Matrix contains 1-based integers (1=A, 2=B, … for PWC).
    # QgsColorRampShader.Discrete assigns a pixel to the first item whose value
    # is >= pixel_value, so item values must equal the integer matrix values.
    if steps and not all(isinstance(s, (int, float)) for s in steps):
        for i, color in enumerate(colors):
            color_qt = QColor(*color) if isinstance(color, list) else QColor(color)
            label = str(steps[i]) if i < len(steps) else f"Class {i + 1}"
            color_items.append(QgsColorRampShader.ColorRampItem(i + 1, color_qt, label))

        shader.setColorRampType(QgsColorRampShader.Discrete)
        shader.setColorRampItemList(color_items)
        cat_vmin = 1.0
        cat_vmax = float(len(colors))
        shader.setMinimumValue(cat_vmin)
        shader.setMaximumValue(cat_vmax)
        return shader, color_items, cat_vmin, cat_vmax

    # ---- numerical values ----
    if steps and len(steps) >= 2:
        vmin, vmax = float(steps[0]), float(steps[-1])
    else:
        if vmin is None or vmax is None:
            vmin, vmax = 0.0, float(len(colors) - 1)

    step_range = vmax - vmin if vmax != vmin else 1.0
    num_colors = len(colors)

    for i, color in enumerate(colors):
        value = vmin + (i / max(1, num_colors - 1)) * step_range
        color_qt = QColor(*color) if isinstance(color, list) else QColor(color)
        label = (
            steps_names[i]
            if i < len(steps_names)
            else (str(steps[i]) if i < len(steps) else f"{value:.2f}")
        )
        color_items.append(QgsColorRampShader.ColorRampItem(value, color_qt, label))

    if interpolation == "binned":
        shader.setColorRampType(QgsColorRampShader.Discrete)
    else:
        shader.setColorRampType(QgsColorRampShader.Interpolated)

    shader.setColorRampItemList(color_items)
    shader.setMinimumValue(vmin)
    shader.setMaximumValue(vmax)
    return shader, color_items, vmin, vmax
