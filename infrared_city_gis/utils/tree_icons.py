"""
Programmatic tree icon generator for the Infrared City GIS plugin.

Icons are rendered at 4× resolution with antialiasing and scaled down to the
requested pixel size, so they look crisp at any DPI. Results are cached so each
(tree_type, size) pair is only painted once per session.
"""

from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap

from ..models.vegetation_types import TreeType

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

_TRUNK = QColor("#5C3317")

_CROWN = {
    TreeType.STONE_PINE: QColor("#1a4a22"),  # evergreen needle – deep fir
    TreeType.PENCIL_TREE: QColor("#8B7355"),  # leafless – tan/bark
    TreeType.MEDITERRANEAN_CYPRESS: QColor("#1e4d1a"),  # evergreen needle – dark
    TreeType.HOLM_OAK: QColor("#1d5c28"),  # evergreen broad – rich green
    TreeType.EUROPEAN_LARCH: QColor("#4a8c3f"),  # deciduous needle – lighter
    TreeType.ENGLISH_OAK: QColor("#3d7a35"),  # deciduous broad
    TreeType.BUSHWILLOW: QColor("#5a8c40"),  # semi-deciduous broad
    TreeType.BALD_CYPRESS: QColor("#4d7a32"),  # semi-deciduous needle
}


# ---------------------------------------------------------------------------
# Per-shape drawing helpers  (all coordinates in 0-1 space × draw_size)
# ---------------------------------------------------------------------------

def _trunk(p: QPainter, s: float, cx_frac: float, bottom_frac: float,
           top_frac: float, w_frac: float):
    """Draw a rectangular trunk."""
    cx = s * cx_frac
    p.setBrush(QBrush(_TRUNK))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRect(QRectF(
        cx - s * w_frac / 2,
        s * top_frac,
        s * w_frac,
        s * (bottom_frac - top_frac),
    ))


def _draw_stone_pine(p: QPainter, s: float):
    """Umbrella / parasol – wide flat crown on a tall slender trunk."""
    crown = _CROWN[TreeType.STONE_PINE]
    # tall thin trunk up to 45 %
    _trunk(p, s, 0.5, 0.97, 0.44, 0.10)
    # wide, low umbrella crown (wider than it is tall)
    p.setBrush(QBrush(crown))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(s * 0.04, s * 0.14, s * 0.92, s * 0.52))


def _draw_pencil_tree(p: QPainter, s: float):
    """Leafless / skeletal – main stem with sparse forking branches."""
    color = _CROWN[TreeType.PENCIL_TREE]
    cx = s * 0.5

    # Main stem
    p.setBrush(QBrush(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRect(QRectF(cx - s * 0.07, s * 0.04, s * 0.14, s * 0.92))

    # Branches (rounded caps)
    pen = QPen(color, s * 0.07)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(cx, s * 0.27), QPointF(cx - s * 0.34, s * 0.10))
    p.drawLine(QPointF(cx, s * 0.42), QPointF(cx + s * 0.34, s * 0.28))
    p.drawLine(QPointF(cx, s * 0.57), QPointF(cx - s * 0.26, s * 0.48))
    p.drawLine(QPointF(cx, s * 0.70), QPointF(cx + s * 0.22, s * 0.63))


def _draw_mediterranean_cypress(p: QPainter, s: float):
    """Narrow column / teardrop – the classic Italian cypress silhouette."""
    crown = _CROWN[TreeType.MEDITERRANEAN_CYPRESS]
    cx = s * 0.5
    path = QPainterPath()
    path.moveTo(cx, s * 0.02)                                    # tip
    path.cubicTo(cx + s * 0.26, s * 0.18,
                 cx + s * 0.20, s * 0.72, cx, s * 0.97)          # right side
    path.cubicTo(cx - s * 0.20, s * 0.72,
                 cx - s * 0.26, s * 0.18, cx, s * 0.02)          # left side
    p.setBrush(QBrush(crown))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(path)


def _draw_holm_oak(p: QPainter, s: float):
    """Dense round crown on a short trunk – Mediterranean evergreen oak."""
    crown = _CROWN[TreeType.HOLM_OAK]
    _trunk(p, s, 0.5, 0.97, 0.58, 0.13)
    p.setBrush(QBrush(crown))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(s * 0.06, s * 0.05, s * 0.88, s * 0.80))


def _draw_european_larch(p: QPainter, s: float):
    """Tall pyramid / triangle – classic conifer silhouette."""
    crown = _CROWN[TreeType.EUROPEAN_LARCH]
    _trunk(p, s, 0.5, 0.97, 0.82, 0.10)
    path = QPainterPath()
    path.moveTo(s * 0.50, s * 0.02)   # apex
    path.lineTo(s * 0.96, s * 0.82)   # bottom-right
    path.lineTo(s * 0.04, s * 0.82)   # bottom-left
    path.closeSubpath()
    p.setBrush(QBrush(crown))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(path)


def _draw_english_oak(p: QPainter, s: float):
    """Broad spreading crown – the archetypal deciduous oak."""
    crown = _CROWN[TreeType.ENGLISH_OAK]
    _trunk(p, s, 0.5, 0.97, 0.56, 0.14)
    p.setBrush(QBrush(crown))
    p.setPen(Qt.PenStyle.NoPen)
    # Very wide ellipse
    p.drawEllipse(QRectF(s * 0.02, s * 0.10, s * 0.96, s * 0.68))


def _draw_bushwillow(p: QPainter, s: float):
    """Medium irregular oval crown – multi-stemmed tropical tree."""
    crown = _CROWN[TreeType.BUSHWILLOW]
    _trunk(p, s, 0.5, 0.97, 0.54, 0.12)
    p.setBrush(QBrush(crown))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(s * 0.13, s * 0.10, s * 0.74, s * 0.66))


def _draw_bald_cypress(p: QPainter, s: float):
    """Narrow cone – slightly wider at base than a larch."""
    crown = _CROWN[TreeType.BALD_CYPRESS]
    _trunk(p, s, 0.5, 0.97, 0.82, 0.10)
    path = QPainterPath()
    path.moveTo(s * 0.50, s * 0.02)   # apex
    path.lineTo(s * 0.84, s * 0.82)   # bottom-right
    path.lineTo(s * 0.16, s * 0.82)   # bottom-left
    path.closeSubpath()
    p.setBrush(QBrush(crown))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(path)


_DRAW_FUNCS = {
    TreeType.STONE_PINE: _draw_stone_pine,
    TreeType.PENCIL_TREE: _draw_pencil_tree,
    TreeType.MEDITERRANEAN_CYPRESS: _draw_mediterranean_cypress,
    TreeType.HOLM_OAK: _draw_holm_oak,
    TreeType.EUROPEAN_LARCH: _draw_european_larch,
    TreeType.ENGLISH_OAK: _draw_english_oak,
    TreeType.BUSHWILLOW: _draw_bushwillow,
    TreeType.BALD_CYPRESS: _draw_bald_cypress,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_cache: dict = {}


def make_tree_icon(tree_type: TreeType, size: int = 24) -> QPixmap:
    """Return a QPixmap icon for *tree_type* at *size* × *size* pixels.

    The icon is rendered at 4× the requested size with antialiasing enabled,
    then scaled down smoothly – giving clean results even at 24 px.
    Results are cached per (tree_type, size) pair.
    """
    key = (tree_type, size)
    if key in _cache:
        return _cache[key]

    draw_size = size * 4
    pixmap = QPixmap(draw_size, draw_size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    draw_fn = _DRAW_FUNCS.get(tree_type)
    if draw_fn:
        draw_fn(painter, float(draw_size))

    painter.end()

    result = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    _cache[key] = result
    return result
