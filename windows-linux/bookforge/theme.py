"""The look of the Mac app on Windows and Linux: a see-through, blurred sidebar (Windows 11), rounded controls,
capsule toolbars and search fields, the system accent blue, and line icons drawn in code.

    theme.apply(app)        once, before any window is made
    theme.frost(window)     on each top-level window, before it is shown

Set BOOKFORGE_BACKDROP to acrylic (the default), mica or none to choose the Windows 11 window backdrop."""
import os, sys

from .qt import (QApplication, QColor, QEvent, QFont, QFontDatabase, QHBoxLayout, QIcon, QLabel, QMenu, QObject,
                 QPainter, QPainterPath, QPalette, QPen, QPixmap, QPointF, QRectF, QSize, Qt, QWidget)
from .util import cache_dir

WINDOWS = sys.platform == 'win32'

LIGHT = dict(
    accent='#007aff', accent_hover='#1a88ff', accent_pressed='#0062cc', on_accent='#ffffff',
    text='#1d1d1f', secondary='#86868b', tertiary='#b0b0b5',
    base='#ffffff', window='#f2f2f4', sidebar='#e9e9ec', sidebar_glass='rgba(236, 236, 240, 150)',
    control='#e8e8ea', control_hover='#dedee1', control_pressed='#d1d1d6',
    field='#ffffff', field_border='#d2d2d7', capsule='#ffffff', capsule_border='rgba(0, 0, 0, 25)',
    separator='rgba(0, 0, 0, 22)', hover='rgba(0, 0, 0, 13)', select_quiet='rgba(0, 0, 0, 24)',
    scroll='rgba(0, 0, 0, 70)', scroll_hover='rgba(0, 0, 0, 110)', menu='#f6f6f8', tip='#ffffff',
    dark=False)
DARK = dict(
    accent='#0a84ff', accent_hover='#2a95ff', accent_pressed='#0a6fd6', on_accent='#ffffff',
    text='#f5f5f7', secondary='#98989d', tertiary='#636366',
    base='#1e1e1e', window='#262628', sidebar='#2b2b2e', sidebar_glass='rgba(40, 40, 44, 150)',
    control='#3a3a3c', control_hover='#444447', control_pressed='#505053',
    field='#2a2a2c', field_border='#3e3e42', capsule='#2c2c2e', capsule_border='rgba(255, 255, 255, 28)',
    separator='rgba(255, 255, 255, 26)', hover='rgba(255, 255, 255, 16)', select_quiet='rgba(255, 255, 255, 30)',
    scroll='rgba(255, 255, 255, 70)', scroll_hover='rgba(255, 255, 255, 115)', menu='#2c2c2e', tip='#2c2c2e',
    dark=True)

C = dict(LIGHT)          # the colours in use; theme.C['accent'] and so on
_glass = False           # whether windows are see-through (Windows 11 only)


def is_dark():
    app = QApplication.instance()
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def glass():
    return _glass


def _windows_build():
    try:
        return sys.getwindowsversion().build
    except AttributeError:
        return 0


def _backdrop_kind():
    """2 Mica, 3 Acrylic (DWM_SYSTEMBACKDROP_TYPE), or 0 for none."""
    if not WINDOWS or _windows_build() < 22621:     # backdrops came in Windows 11 22H2
        return 0
    return {'none': 0, 'mica': 2, 'acrylic': 3}.get(os.environ.get('BOOKFORGE_BACKDROP', 'acrylic').lower(), 3)


# MARK: icons

def _path_icon(name, color, size=20, width=1.6):
    """One line icon drawn at 2x, in the colour given."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = size / 20
    p.scale(s, s)
    P = QPointF

    def path(*pts, close=False):
        pa = QPainterPath(P(*pts[0]))
        for pt in pts[1:]:
            pa.lineTo(P(*pt))
        if close:
            pa.closeSubpath()
        p.drawPath(pa)

    def arrow_down(x, y0, y1):
        path((x, y0), (x, y1))
        path((x - 3, y1 - 3), (x, y1), (x + 3, y1 - 3))

    def arrow_up(x, y0, y1):
        path((x, y0), (x, y1))
        path((x - 3, y1 + 3), (x, y1), (x + 3, y1 + 3))

    if name == 'books':
        p.drawRoundedRect(QRectF(2.5, 3, 4, 14), 1, 1)
        p.drawRoundedRect(QRectF(7.5, 5, 4, 12), 1, 1)
        p.save()
        p.translate(14.2, 4.6)
        p.rotate(-14)
        p.drawRoundedRect(QRectF(0, 0, 3.6, 12.6), 1, 1)
        p.restore()
        path((3.8, 6), (5.2, 6))
        path((8.8, 8), (10.2, 8))
    elif name == 'sparkles':
        path((3.5, 16.5), (11.5, 8.5))
        star = QPainterPath()
        for cx, cy, r in ((14.5, 5.5, 3.6), (6, 5, 2.2), (15.5, 13.5, 2)):
            star.moveTo(cx, cy - r)
            star.quadTo(cx, cy, cx + r, cy)
            star.quadTo(cx, cy, cx, cy + r)
            star.quadTo(cx, cy, cx - r, cy)
            star.quadTo(cx, cy, cx, cy - r)
        p.setBrush(QColor(color))
        p.drawPath(star)
    elif name == 'sort':
        arrow_up(6.5, 16, 4)
        arrow_down(13.5, 4, 16)
    elif name == 'import':
        path((4, 10), (4, 16.5), (16, 16.5), (16, 10))
        arrow_down(10, 3, 12.5)
    elif name == 'send':
        path((7, 8), (4.5, 8), (4.5, 17), (15.5, 17), (15.5, 8), (13, 8))
        arrow_up(10, 12.5, 2.5)
    elif name == 'plus':
        path((10, 3.5), (10, 16.5))
        path((3.5, 10), (16.5, 10))
    elif name == 'search':
        p.drawEllipse(QRectF(3, 3, 10, 10))
        path((11.6, 11.6), (16.5, 16.5))
    elif name == 'refresh':
        pa = QPainterPath()
        pa.arcMoveTo(QRectF(3.5, 3.5, 13, 13), 60)
        pa.arcTo(QRectF(3.5, 3.5, 13, 13), 60, 300)
        p.drawPath(pa)
        path((10.6, 2.6), (13.4, 4.4), (11.6, 7.2))
    elif name == 'eject':
        path((10, 3.5), (16, 11), (4, 11), close=True)
        path((4, 15.5), (16, 15.5))
    elif name == 'sidebar':
        p.drawRoundedRect(QRectF(2.5, 4, 15, 12), 3, 3)
        path((7.5, 4), (7.5, 16))
    elif name == 'chevrons':        # the up-and-down arrows of a pop-up menu
        path((7, 8), (10, 5), (13, 8))
        path((7, 12), (10, 15), (13, 12))
    elif name == 'chevron_down':
        path((6, 8), (10, 12), (14, 8))
    elif name == 'chevron_up':
        path((6, 12), (10, 8), (14, 12))
    elif name == 'check':
        path((5, 10.5), (8.5, 14), (15, 6.5))
    elif name == 'dash':
        path((5.5, 10), (14.5, 10))
    elif name == 'doc':
        path((5, 2.5), (11.5, 2.5), (15, 6), (15, 17.5), (5, 17.5), close=True)
        path((11.5, 2.5), (11.5, 6), (15, 6))
    p.end()
    return pm


def icon(name, color=None, selected=None, size=20):
    """A QIcon in the text colour (or the colour given), and white or `selected` when its row is selected."""
    ic = QIcon()
    ic.addPixmap(_path_icon(name, color or C['text'], size), QIcon.Mode.Normal)
    ic.addPixmap(_path_icon(name, color or C['tertiary'], size), QIcon.Mode.Disabled)
    if selected:
        ic.addPixmap(_path_icon(name, selected, size), QIcon.Mode.Selected)
    return ic


def _write_images():
    """Check marks and arrows for the style sheet, which only takes image files."""
    folder = os.path.join(cache_dir(), 'theme', 'dark' if C['dark'] else 'light')
    os.makedirs(folder, exist_ok=True)
    out = {}
    for key, name, color, size, width in (
            ('check', 'check', C['on_accent'], 14, 2.2), ('dash', 'dash', C['on_accent'], 14, 2.2),
            ('chevrons', 'chevrons', C['secondary'], 16, 1.6), ('down', 'chevron_down', C['secondary'], 12, 1.8),
            ('up', 'chevron_up', C['secondary'], 12, 1.8), ('branch_closed', 'chevron_down', C['secondary'], 12, 1.8)):
        pm = _path_icon(name, color, size, width)
        if key == 'branch_closed':
            from .qt import QTransform
            pm = pm.transformed(QTransform().rotate(-90))
        f = os.path.join(folder, key + '@2x.png')
        pm.save(f)
        pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation).save(
            os.path.join(folder, key + '.png'))
        out[key] = os.path.join(folder, key + '.png').replace('\\', '/')
    return out


# MARK: palette and style sheet

def _palette():
    pal = QPalette()
    q = lambda k: QColor(C[k])
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        pal.setColor(group, QPalette.ColorRole.Window, q('window'))
        pal.setColor(group, QPalette.ColorRole.WindowText, q('text'))
        pal.setColor(group, QPalette.ColorRole.Base, q('base'))
        pal.setColor(group, QPalette.ColorRole.AlternateBase, q('window'))
        pal.setColor(group, QPalette.ColorRole.Text, q('text'))
        pal.setColor(group, QPalette.ColorRole.Button, q('control'))
        pal.setColor(group, QPalette.ColorRole.ButtonText, q('text'))
        pal.setColor(group, QPalette.ColorRole.Highlight, q('accent'))
        pal.setColor(group, QPalette.ColorRole.HighlightedText, q('on_accent'))
        pal.setColor(group, QPalette.ColorRole.ToolTipBase, q('tip'))
        pal.setColor(group, QPalette.ColorRole.ToolTipText, q('text'))
        pal.setColor(group, QPalette.ColorRole.PlaceholderText, q('secondary'))
        pal.setColor(group, QPalette.ColorRole.Link, q('accent'))
        pal.setColor(group, QPalette.ColorRole.Mid, q('tertiary'))
        pal.setColor(group, QPalette.ColorRole.Light, q('base'))
        pal.setColor(group, QPalette.ColorRole.Midlight, q('control'))
        pal.setColor(group, QPalette.ColorRole.Dark, q('control_pressed'))
    for role, key in ((QPalette.ColorRole.WindowText, 'tertiary'), (QPalette.ColorRole.Text, 'tertiary'),
                      (QPalette.ColorRole.ButtonText, 'tertiary'), (QPalette.ColorRole.Highlight, 'control_pressed')):
        pal.setColor(QPalette.ColorGroup.Disabled, role, q(key))
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        pal.setColor(QPalette.ColorGroup.Disabled, role, pal.color(QPalette.ColorGroup.Active, role))
    return pal


QSS = """
QWidget { color: %(text)s; }
QMainWindow, QDialog, QMessageBox { background: %(window)s; }
QMainWindow[bfGlass="true"] { background: transparent; }
#contentPane { background: %(base)s; }
#contentPane QScrollArea, #contentPane QScrollArea > QWidget > QWidget { background: %(base)s; }
#sidebarPane { background: %(sidebar_bg)s; }
QSplitter::handle { background: %(separator)s; }
QSplitter::handle:horizontal { width: 1px; }

/* sidebar */
QListWidget#sidebar { background: transparent; border: none; outline: none; padding: 4px 0; }
QListWidget#sidebar::item { border-radius: 8px; margin: 1px 10px; padding: 0 6px; color: %(text)s; }
QListWidget#sidebar::item:disabled { color: %(secondary)s; background: transparent; }
QListWidget#sidebar::item:hover:!selected { background: %(hover)s; }
QListWidget#sidebar::item:selected { background: %(select_quiet)s; color: %(text)s; }
QListWidget#sidebar[focused="true"]::item:selected { background: %(accent)s; color: %(on_accent)s; }

/* headers */
#header { background: %(base)s; }
#headerTitle { color: %(text)s; }
QLabel[secondary="true"] { color: %(secondary)s; }

/* buttons */
QPushButton {
    background: %(control)s; color: %(text)s; border: none; border-radius: 8px;
    padding: 6px 16px; min-height: 18px; }
QPushButton:hover { background: %(control_hover)s; }
QPushButton:pressed { background: %(control_pressed)s; }
QPushButton:disabled { color: %(tertiary)s; background: %(control)s; }
QPushButton:default, QPushButton[primary="true"] { background: %(accent)s; color: %(on_accent)s; }
QPushButton:default:hover, QPushButton[primary="true"]:hover { background: %(accent_hover)s; }
QPushButton:default:pressed, QPushButton[primary="true"]:pressed { background: %(accent_pressed)s; }
QPushButton:default:disabled, QPushButton[primary="true"]:disabled { background: %(control)s; color: %(tertiary)s; }
QPushButton:flat { background: transparent; color: %(accent)s; padding: 2px 6px; }
QPushButton:flat:hover { background: %(hover)s; }
QPushButton:flat:disabled { color: %(tertiary)s; }
QPushButton:focus { outline: none; }
QPushButton::menu-indicator { width: 0; }

QToolButton { background: transparent; border: none; border-radius: 8px; padding: 5px 7px; color: %(text)s; }
QToolButton:hover { background: %(hover)s; }
QToolButton:pressed, QToolButton:checked { background: %(select_quiet)s; }
QToolButton::menu-indicator { image: none; width: 0; }
QToolButton[link="true"] { color: %(secondary)s; padding: 2px 0; }
QToolButton[link="true"]:hover, QToolButton[link="true"]:checked { background: transparent; color: %(text)s; }

/* capsule toolbars, like the Mac's */
#capsule { background: %(capsule)s; border: 1px solid %(capsule_border)s; border-radius: 19px; }
#capsule QToolButton { border-radius: 15px; padding: 6px 9px; }

/* text fields */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
    background: %(field)s; border: 1px solid %(field_border)s; border-radius: 8px;
    padding: 5px 8px; selection-background-color: %(accent)s; selection-color: %(on_accent)s; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus { border: 2px solid %(accent)s; padding: 4px 7px; }
QLineEdit:disabled, QSpinBox:disabled { color: %(tertiary)s; }
QLineEdit#search { background: %(capsule)s; border: 1px solid %(capsule_border)s; border-radius: 19px;
    padding: 7px 12px 7px 6px; min-height: 22px; }
QLineEdit#search:focus { border: 2px solid %(accent)s; padding: 6px 11px 6px 5px; }
QSpinBox { padding-right: 22px; }
QSpinBox::up-button, QSpinBox::down-button { subcontrol-origin: border; width: 20px; border: none; background: transparent; }
QSpinBox::up-button { subcontrol-position: top right; }
QSpinBox::down-button { subcontrol-position: bottom right; }
QSpinBox::up-arrow { image: url(%(img_up)s); width: 10px; height: 10px; }
QSpinBox::down-arrow { image: url(%(img_down)s); width: 10px; height: 10px; }

/* pop-up menus */
QComboBox { background: %(control)s; border: none; border-radius: 8px; padding: 6px 30px 6px 12px; min-height: 18px; }
QComboBox:hover { background: %(control_hover)s; }
QComboBox:disabled { color: %(tertiary)s; }
QComboBox::drop-down { border: none; width: 26px; subcontrol-position: center right; }
QComboBox::down-arrow { image: url(%(img_chevrons)s); width: 14px; height: 14px; }
QComboBox QAbstractItemView { background: %(menu)s; border: %(popup_border)s; border-radius: %(popup_radius)s;
    padding: 4px; outline: none; selection-background-color: %(accent)s; selection-color: %(on_accent)s; }
QComboBox QAbstractItemView::item { min-height: 24px; padding: 0 8px; border-radius: 5px; }

QMenu { background: %(menu)s; border: %(popup_border)s; border-radius: %(popup_radius)s; padding: 5px; }
QMenu::item { padding: 5px 22px 5px 22px; border-radius: 6px; color: %(text)s; }
QMenu::item:selected { background: %(accent)s; color: %(on_accent)s; }
QMenu::item:disabled { color: %(tertiary)s; background: transparent; }
QMenu::separator { height: 1px; background: %(separator)s; margin: 5px 8px; }
QMenu::indicator { width: 14px; height: 14px; left: 4px; }
QMenu::indicator:checked { image: url(%(img_check_dark)s); }
QMenuBar { background: transparent; border: none; padding: 2px 6px; }
QMenuBar::item { background: transparent; padding: 4px 10px; border-radius: 6px; color: %(text)s; }
QMenuBar::item:selected { background: %(hover)s; }
QMenuBar::item:pressed { background: %(select_quiet)s; }

/* checkboxes, radio buttons */
QCheckBox, QRadioButton { spacing: 8px; background: transparent; }
QCheckBox::indicator, QTreeView::indicator, QListView::indicator {
    width: 16px; height: 16px; border-radius: 5px; border: 1px solid %(field_border)s; background: %(field)s; }
QCheckBox::indicator:hover, QTreeView::indicator:hover { border-color: %(accent)s; }
QCheckBox::indicator:checked, QTreeView::indicator:checked, QListView::indicator:checked {
    background: %(accent)s; border-color: %(accent)s; image: url(%(img_check)s); }
QCheckBox::indicator:indeterminate, QTreeView::indicator:indeterminate {
    background: %(accent)s; border-color: %(accent)s; image: url(%(img_dash)s); }
QCheckBox::indicator:disabled { background: %(control)s; border-color: %(control)s; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 9px; border: 1px solid %(field_border)s; background: %(field)s; }
QRadioButton::indicator:checked { border: 5px solid %(accent)s; background: %(on_accent)s; width: 8px; height: 8px; }

/* segmented control (radio buttons with the segment property) */
#segments { background: %(control)s; border-radius: 8px; }
QRadioButton[segment="true"] { padding: 6px 18px; border-radius: 7px; margin: 0; }
QRadioButton[segment="true"]::indicator { width: 0; height: 0; border: none; background: transparent; image: none; }
QRadioButton[segment="true"]:hover:!checked { background: %(control_hover)s; }
QRadioButton[segment="true"]:checked { background: %(accent)s; color: %(on_accent)s; }

/* group boxes become plain sections, as in the Mac's forms */
QGroupBox { border: none; margin-top: 26px; padding-top: 4px; font-weight: 600; background: transparent; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 2px; padding: 0; color: %(text)s; }

/* lists */
QAbstractItemView { background: %(base)s; border: none; outline: none;
    selection-background-color: %(accent)s; selection-color: %(on_accent)s; }
QTreeWidget#books::item { padding: 0 2px; border: none; border-bottom: 1px solid %(separator)s; }
QTreeWidget#books::item:hover { background: transparent; }
QTreeWidget#books::item:selected { background: transparent; color: %(text)s; }
QTreeView::branch { background: transparent; image: none; border: none; }
QHeaderView::section { background: %(base)s; border: none; border-bottom: 1px solid %(separator)s; padding: 4px 8px; }

/* progress, sliders */
QProgressBar { background: %(control)s; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; }
QProgressBar::chunk { background: %(accent)s; border-radius: 4px; }
QProgressBar[gauge="true"] { border-radius: 5px; max-height: 10px; min-height: 10px; }
QProgressBar[gauge="true"]::chunk { background: #34c759; border-radius: 5px; }
QSlider::groove:horizontal { height: 4px; background: %(control_pressed)s; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %(accent)s; border-radius: 2px; }
QSlider::handle:horizontal { background: #ffffff; border: 1px solid %(field_border)s; width: 18px; height: 18px;
    margin: -8px 0; border-radius: 10px; }

/* thin, rounded scroll bars */
QScrollBar:vertical { background: transparent; width: 12px; margin: 2px 2px 2px 0; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 0 2px 2px 2px; }
QScrollBar::handle:vertical { background: %(scroll)s; border-radius: 4px; min-height: 36px; margin: 0 2px; }
QScrollBar::handle:horizontal { background: %(scroll)s; border-radius: 4px; min-width: 36px; margin: 2px 0; }
QScrollBar::handle:hover { background: %(scroll_hover)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; background: transparent; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QToolTip { background: %(tip)s; color: %(text)s; border: %(popup_border)s; border-radius: %(popup_radius)s; padding: 5px 8px; }
QToolBar { background: transparent; border: none; spacing: 4px; padding: 4px 8px; }
"""


def stylesheet():
    imgs = _write_images()
    # Windows 11 rounds pop-ups itself (see _RoundPopups) and draws their border; elsewhere they stay square
    round_popups = WINDOWS and _windows_build() >= 22000
    vals = dict(C, popup_radius='8px' if round_popups else '0', popup_border='none' if round_popups else '1px solid ' + C['separator'],
                sidebar_bg=C['sidebar_glass'] if _glass else C['sidebar'],
                img_check=imgs['check'], img_dash=imgs['dash'], img_chevrons=imgs['chevrons'],
                img_up=imgs['up'], img_down=imgs['down'], img_check_dark=_check_for_menu())
    return QSS % vals


def _check_for_menu():
    folder = os.path.join(cache_dir(), 'theme', 'dark' if C['dark'] else 'light')
    os.makedirs(folder, exist_ok=True)
    pm = _path_icon('check', C['text'], 14, 2)
    f = os.path.join(folder, 'menucheck@2x.png')
    pm.save(f)
    pm.scaled(14, 14, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation).save(
        os.path.join(folder, 'menucheck.png'))
    return os.path.join(folder, 'menucheck.png').replace('\\', '/')


def _font():
    f = QApplication.font()
    families = set(QFontDatabase.families())
    for name in ('Segoe UI Variable Text', 'Segoe UI Variable Display', 'Segoe UI', 'Inter', 'Cantarell', 'Noto Sans'):
        if name in families:
            f.setFamily(name)
            break
    if WINDOWS:
        f.setPointSizeF(10)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting if not WINDOWS else QFont.HintingPreference.PreferVerticalHinting)
    return f


_windows = []


def _restyle():
    global C
    C = dict(DARK if is_dark() else LIGHT)
    app = QApplication.instance()
    app.setPalette(_palette())
    app.setStyleSheet(stylesheet())
    for w in list(_windows):
        try:
            _dwm(w)
        except RuntimeError:          # the window is gone
            _windows.remove(w)
    for w in app.topLevelWidgets():
        if hasattr(w, 'theme_changed'):
            w.theme_changed()


def apply(app):
    """The Fusion style dressed like the Mac app, in light or dark following the system."""
    global _glass
    app.setStyle('Fusion')
    app.setFont(_font())
    _glass = _backdrop_kind() != 0
    _restyle()
    try:
        app.styleHints().colorSchemeChanged.connect(lambda *_: _restyle())
    except AttributeError:
        pass
    if WINDOWS and _windows_build() >= 22000:
        app.installEventFilter(_RoundPopups(app))


# MARK: Windows 11 window effects

def _dwm_set(hwnd, attr, value):
    import ctypes
    from ctypes import wintypes
    v = ctypes.c_int(value)
    return ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), wintypes.DWORD(attr), ctypes.byref(v),
                                                      ctypes.sizeof(v))


def _dwm(window, backdrop=True):
    if not WINDOWS:
        return
    hwnd = int(window.winId())
    build = _windows_build()
    if build >= 17763:
        _dwm_set(hwnd, 20 if build >= 18985 else 19, 1 if C['dark'] else 0)    # DWMWA_USE_IMMERSIVE_DARK_MODE
    if build >= 22000:
        _dwm_set(hwnd, 33, 2)                     # DWMWA_WINDOW_CORNER_PREFERENCE: DWMWCP_ROUND
    kind = _backdrop_kind() if backdrop and window.property('bfGlass') else 0
    if kind:
        import ctypes
        from ctypes import wintypes

        class MARGINS(ctypes.Structure):
            _fields_ = [('l', ctypes.c_int), ('r', ctypes.c_int), ('t', ctypes.c_int), ('b', ctypes.c_int)]
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(MARGINS(-1, -1, -1, -1)))
        _dwm_set(hwnd, 38, kind)                  # DWMWA_SYSTEMBACKDROP_TYPE


def frost(window, see_through=True):
    """Rounded corners and a title bar matching light or dark on Windows 11; with see_through, the blurred
    desktop shows through wherever the window paints nothing (the sidebar). Call before the window is shown."""
    if see_through and _glass:
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        window.setProperty('bfGlass', True)
    if WINDOWS:
        try:
            _dwm(window)
            _windows.append(window)
        except Exception as e:      # never let looks stop the app
            print('window effects:', e)


class _RoundPopups(QObject):
    """Rounded corners for menus and pop-up lists, as Windows 11 draws its own."""
    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow() and \
                (isinstance(obj, QMenu) or obj.inherits('QComboBoxPrivateContainer') or obj.inherits('QToolTip')
                 or obj.metaObject().className() == 'QTipLabel'):
            try:
                _dwm_set(int(obj.winId()), 33, 2)     # DWMWCP_ROUND, the 8px the style sheet uses
            except Exception:
                pass
        return False


# MARK: shared widgets

class Header(QWidget):
    """The bar along the top of each screen: a title on the left, controls on the right, a hairline below."""
    def __init__(self, title='', divider=True):
        super().__init__()
        self.setObjectName('header')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.divider = divider
        self.setFixedHeight(60)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(22, 10, 14, 10)
        self.row.setSpacing(10)
        self.title = QLabel(title)
        self.title.setObjectName('headerTitle')
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.35)
        f.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(f)
        self.row.addWidget(self.title)
        self.row.addStretch(1)

    def add(self, w):
        self.row.addWidget(w)
        return w

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.divider:
            p = QPainter(self)
            c = QColor(0, 0, 0, 22) if not C['dark'] else QColor(255, 255, 255, 26)
            p.fillRect(0, self.height() - 1, self.width(), 1, c)


def capsule(*buttons):
    """Tool buttons grouped in one rounded capsule, like a Mac toolbar group."""
    from .qt import QFrame
    box = QFrame()
    box.setObjectName('capsule')
    h = QHBoxLayout(box)
    h.setContentsMargins(3, 2, 3, 2)
    h.setSpacing(0)
    for b in buttons:
        h.addWidget(b)
    return box


def tool(name, tip, slot=None):
    from .qt import QToolButton
    b = QToolButton()
    b.setIconSize(QSize(20, 20))
    b.setToolTip(tip)
    b.setProperty('iconName', name)
    b.setIcon(icon(name))
    if slot:
        b.clicked.connect(slot)
    return b


def search_field(placeholder):
    from .qt import QLineEdit
    e = QLineEdit(placeholderText=placeholder)
    e.setObjectName('search')
    e.setClearButtonEnabled(True)
    a = e.addAction(icon('search', C['secondary'], size=18), QLineEdit.ActionPosition.LeadingPosition)
    e.setProperty('searchAction', True)
    e._search_action = a
    return e


def retint(root):
    """Redraws the line icons under `root` in the current colours (after light/dark changes)."""
    from .qt import QAbstractButton, QLineEdit
    for b in root.findChildren(QAbstractButton):
        name = b.property('iconName')
        if name:
            b.setIcon(icon(name, C['accent'] if b.property('iconAccent') else None))
    for e in root.findChildren(QLineEdit):
        if getattr(e, '_search_action', None):
            e._search_action.setIcon(icon('search', C['secondary'], size=18))
