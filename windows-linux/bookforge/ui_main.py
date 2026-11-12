"""The main window: sidebar (Library, the e-reader, Convert & Translate) and the three screens."""
import os

from .qt import (QApplication, QColor, QFont, QHBoxLayout, QIcon, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                 QMessageBox, QRectF, QSize, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
                 Qt, QVBoxLayout, QWidget)

from . import VERSION, theme
from . import devices as dev
from . import library as lib
from .deviceart import DeviceArt
from .ui_device import DeviceView
from .ui_forge import ForgeView
from .ui_library import LibraryView

COUNT = Qt.ItemDataRole.UserRole + 1


class DeviceRow(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        h = QHBoxLayout(self)
        h.setContentsMargins(20, 4, 18, 4)
        h.setSpacing(10)
        self.art = DeviceArt('generic', (18, 26))
        h.addWidget(self.art)
        v = QVBoxLayout()
        v.setSpacing(0)
        self.title = QLabel()
        self.sub = QLabel()
        f = self.sub.font()
        f.setPointSizeF(f.pointSizeF() * 0.85)
        self.sub.setFont(f)
        v.addWidget(self.title)
        v.addWidget(self.sub)
        h.addLayout(v, 1)
        self.spin = QLabel('⏳')
        h.addWidget(self.spin)
        self.highlight(False)

    def highlight(self, on):
        """White text on the accent colour when the row is selected and the sidebar has focus."""
        self.title.setStyleSheet('color: %s; background: transparent;' % (theme.C['on_accent'] if on else theme.C['text']))
        self.sub.setStyleSheet('color: %s; background: transparent;' % ('rgba(255,255,255,200)' if on else theme.C['secondary']))

    def refresh(self):
        D = dev.shared()
        st = D.state
        self.art.set(D.reader['kind'] if D.reader else ('generic' if st == 'none' else D.busy_kind), dim=D.reader is None)
        self.title.setText(D.reader['name'] if st == 'connected' and D.reader else
                           D.state_name if st in ('busy', 'ejected') else 'E-reader')
        self.sub.setText({'connected': '%d books' % len(D.reader['books']) if D.reader else '',
                          'busy': 'In use by Calibre', 'ejected': 'Safe to unplug', 'failed': "Couldn't connect",
                          'none': 'Not connected'}.get(st, ''))
        self.spin.setVisible(D.scanning or D.working is not None)


class SidebarDelegate(QStyledItemDelegate):
    """The usual row, plus the book count on the right, as in the Mac sidebar."""
    def paint(self, p, option, index):
        super().paint(p, option, index)
        n = index.data(COUNT)
        if n is None:
            return
        view = self.parent()
        lit = bool(option.state & QStyle.StateFlag.State_Selected) and view.property('focused')
        p.save()
        p.setPen(QColor(theme.C['on_accent'] if lit else theme.C['secondary']))
        f = QFont(option.font)
        f.setPointSizeF(f.pointSizeF() * 0.9)
        p.setFont(f)
        p.drawText(QRectF(option.rect).adjusted(0, 0, -26, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(n))
        p.restore()


class Sidebar(QListWidget):
    """Shows its selection in the accent colour only while it has the focus, like the Mac's source lists."""
    def __init__(self):
        super().__init__()
        self.setObjectName('sidebar')
        self.setProperty('focused', False)
        self.setItemDelegate(SidebarDelegate(self))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.on_focus = None

    def _focus(self, on):
        self.setProperty('focused', on)
        self.style().unpolish(self)
        self.style().polish(self)
        self.viewport().update()
        if self.on_focus:
            self.on_focus()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self._focus(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._focus(False)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Book Forge')
        theme.frost(self)
        icon = os.path.join(os.path.dirname(__file__), 'icon.png')
        if os.path.exists(icon):
            self.setWindowIcon(QIcon(icon))
        self.resize(1080, 720)
        self.setMinimumSize(860, 560)
        self.L, self.D = lib.shared(), dev.shared()

        self.side = Sidebar()
        self.side.setIconSize(QSize(20, 20))

        def section(text):
            it = QListWidgetItem(text)
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
            it.setSizeHint(QSize(200, 34))
            f = it.font()
            f.setWeight(QFont.Weight.DemiBold)
            f.setPointSizeF(f.pointSizeF() * 0.85)
            it.setFont(f)
            self.side.addItem(it)

        def entry(text):
            it = QListWidgetItem('  ' + text)
            it.setSizeHint(QSize(200, 36))
            self.side.addItem(it)
            return it
        section('Library')
        self.all_item = entry('All Books')
        section('Devices')
        self.dev_item = QListWidgetItem()
        self.dev_row = DeviceRow()
        self.dev_item.setSizeHint(QSize(200, 48))
        self.side.addItem(self.dev_item)
        self.side.setItemWidget(self.dev_item, self.dev_row)
        section('Tools')
        self.forge_item = entry('Convert & Translate')
        self.side.on_focus = self.highlight_device

        self.stack = QStackedWidget()
        self.library = LibraryView(self)
        self.device = DeviceView(self)
        self.forge = ForgeView(self)
        for w in (self.library, self.device, self.forge):
            self.stack.addWidget(w)
        self.side.currentItemChanged.connect(self.select)
        self.side.setCurrentItem(self.all_item)

        # the sidebar sits on the window's backdrop (blurred on Windows 11); the screens on a solid page
        side_pane = QWidget()
        side_pane.setObjectName('sidebarPane')
        side_pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        sv = QVBoxLayout(side_pane)
        sv.setContentsMargins(0, 8, 0, 8)
        sv.addWidget(self.side)
        side_pane.setMinimumWidth(210)
        side_pane.setMaximumWidth(320)
        content = QWidget()
        content.setObjectName('contentPane')
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self.stack)

        split = QSplitter()
        split.addWidget(side_pane)
        split.addWidget(content)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 850])
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)
        self.setCentralWidget(split)

        file_menu = self.menuBar().addMenu('&File')
        file_menu.addAction('Add Books…', self.library.choose)
        file_menu.addSeparator()
        file_menu.addAction('Quit', QApplication.instance().quit)
        help_menu = self.menuBar().addMenu('&Help')
        help_menu.addAction('About Book Forge', lambda: QMessageBox.about(
            self, 'About Book Forge', 'Book Forge %s\n\nYour e-book library, PDF to EPUB, translation, and Kindle sync.\n'
            'Works with Kindle, Kobo and other e-readers by itself: no other software needed.' % VERSION))

        self.L.changed.connect(self.refresh)
        self.D.changed.connect(self.refresh)
        self.D.ask_quit_calibre.connect(self.ask_quit_calibre)
        self.theme_changed()
        self.refresh()

    def theme_changed(self):
        """Light or dark changed (or first set up): redraw the drawn icons in the new colours."""
        a = theme.C['accent']
        white = theme.C['on_accent']
        self.all_item.setIcon(theme.icon('books', a, white))
        self.forge_item.setIcon(theme.icon('sparkles', a, white))
        theme.retint(self)
        self.highlight_device()

    def highlight_device(self):
        self.dev_row.highlight(self.side.currentItem() is self.dev_item and bool(self.side.property('focused')))

    def select(self, item, _=None):
        if item is self.all_item:
            self.stack.setCurrentWidget(self.library)
        elif item is self.dev_item:
            self.stack.setCurrentWidget(self.device)
        elif item is self.forge_item:
            self.stack.setCurrentWidget(self.forge)
        self.highlight_device()

    def show_device(self):
        self.side.setCurrentItem(self.dev_item)

    def translate(self, book):
        self.forge.load(self.L.url(book), book)
        self.side.setCurrentItem(self.forge_item)

    def refresh(self):
        self.all_item.setData(COUNT, len(self.L.books))
        self.dev_row.refresh()

    def ask_quit_calibre(self):
        r = QMessageBox.question(self, 'Close Calibre?', "The Calibre app is open and holds the e-reader connection. "
                                 "It has to be closed for Book Forge to change what's on the reader.",
                                 QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Ok:
            self.D.quit_calibre_and_continue()
        else:
            self.D.cancel_pending()
