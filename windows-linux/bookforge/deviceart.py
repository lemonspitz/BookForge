"""A drawing of the connected e-reader, shaped after its family, with a book cover on its screen."""
from .qt import QBrush, QColor, QLinearGradient, QPainter, QPen, QRectF, QSize, QWidget, Qt

# aspect (w/h), body grey or rgb, corner (x width), inset top/left/bottom/right (x width), controls
SPECS = {
    'kindle-oasis': (0.80, (56, 56, 56), 0.05, (0.06, 0.06, 0.06, 0.24), 'grip'),
    'kindle-scribe': (0.82, (77, 77, 77), 0.035, (0.045, 0.045, 0.045, 0.13), None),
    'kindle': (0.70, (28, 28, 28), 0.06, (0.075, 0.07, 0.13, 0.07), None),
    'kobo': (0.72, (20, 20, 20), 0.05, (0.065, 0.06, 0.07, 0.06), None),
    'pocketbook': (0.67, (69, 71, 79), 0.06, (0.08, 0.07, 0.19, 0.07), 'row'),
    'boox': (0.73, (43, 43, 43), 0.045, (0.05, 0.05, 0.11, 0.05), 'home'),
}
GENERIC = (0.70, (77, 77, 77), 0.06, (0.07, 0.07, 0.11, 0.07), 'home')


class DeviceArt(QWidget):
    def __init__(self, kind='generic', size=(130, 170), parent=None):
        super().__init__(parent)
        self.kind, self.cover, self.dim = kind, None, False
        self._size = QSize(*size)
        self.setFixedSize(self._size)

    def set(self, kind, cover=None, dim=False):
        self.kind, self.cover, self.dim = kind, cover, dim
        self.update()

    def paintEvent(self, e):
        aspect, body, corner, (it, il, ib, ir), controls = SPECS.get(self.kind, GENERIC)
        W, H = self.width(), self.height()
        w = min(W, H * aspect)
        h = w / aspect
        x0, y0 = (W - w) / 2, (H - h) / 2
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.dim:
            p.setOpacity(0.45)
        g = QLinearGradient(0, y0, 0, y0 + h)
        c = QColor(*body)
        g.setColorAt(0, c.lighter(125))
        g.setColorAt(1, c)
        p.setBrush(QBrush(g))
        p.setPen(QPen(QColor(255, 255, 255, 46), max(0.5, w * 0.006)))
        p.drawRoundedRect(QRectF(x0, y0, w, h), w * corner, w * corner)
        screen = QRectF(x0 + w * il, y0 + w * it, w * (1 - il - ir), h - w * (it + ib))
        sg = QLinearGradient(screen.topLeft(), screen.bottomRight())
        sg.setColorAt(0, QColor(237, 237, 237))
        sg.setColorAt(1, QColor(224, 224, 224))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(sg))
        p.drawRect(screen)
        if self.cover is not None and not self.cover.isNull():
            img = self.cover.toImage().convertToFormat(self.cover.toImage().Format.Format_Grayscale8)
            scaled = img.scaled(screen.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                Qt.TransformationMode.SmoothTransformation)
            sx = (scaled.width() - screen.width()) / 2
            sy = (scaled.height() - screen.height()) / 2
            p.drawImage(screen, scaled, QRectF(sx, sy, screen.width(), screen.height()))
        elif screen.width() > 40:
            p.setBrush(QColor(158, 158, 158))
            lh = max(1.0, screen.height() * 0.012)
            for i in range(9):
                lw = screen.width() * (0.45 if i == 8 else 0.6 if i == 0 else 0.78)
                y = screen.top() + screen.height() * (0.12 + 0.085 * i)
                p.drawRoundedRect(QRectF(screen.left() + screen.width() * 0.11, y, lw, lh), lh / 2, lh / 2)
        p.setBrush(QColor(255, 255, 255, 41))
        if controls == 'grip':
            x = x0 + w * (1 - ir / 2) - w * 0.012
            for i in range(2):
                p.drawRoundedRect(QRectF(x, y0 + h * (0.36 + 0.15 * i), w * 0.024, h * 0.1), w * 0.012, w * 0.012)
        elif controls == 'home':
            p.drawRoundedRect(QRectF(x0 + w * 0.42, y0 + h - w * ib / 2 - w * 0.015, w * 0.16, w * 0.03), w * 0.015, w * 0.015)
        elif controls == 'row':
            y = y0 + h - w * ib / 2 - w * 0.0175
            x = x0 + w * 0.17
            for k in range(5):
                if k == 2:
                    p.drawEllipse(QRectF(x, y - w * 0.0125, w * 0.06, w * 0.06))
                    x += w * 0.11
                else:
                    p.drawRoundedRect(QRectF(x, y, w * 0.12, w * 0.035), w * 0.0175, w * 0.0175)
                    x += w * 0.17
        p.end()
