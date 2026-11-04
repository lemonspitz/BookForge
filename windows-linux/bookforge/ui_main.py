"""The main window: sidebar (Library, the e-reader, Convert & Translate) and the three screens."""
import os

from .qt import (QApplication, QHBoxLayout, QIcon, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                     QSize, QSplitter, QStackedWidget, Qt, QVBoxLayout, QWidget)

from . import VERSION
from . import devices as dev
from . import library as lib
from .deviceart import DeviceArt
from .ui_device import DeviceView
from .ui_forge import ForgeView
from .ui_library import LibraryView


class DeviceRow(QWidget):
    def __init__(self):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 4, 6, 4)
        self.art = DeviceArt('generic', (18, 24))
        h.addWidget(self.art)
        v = QVBoxLayout()
        v.setSpacing(0)
        self.title = QLabel()
        self.sub = QLabel()
        f = self.sub.font()
        f.setPointSizeF(f.pointSizeF() * 0.85)
        self.sub.setFont(f)
        self.sub.setStyleSheet('color: gray;')
        v.addWidget(self.title)
        v.addWidget(self.sub)
        h.addLayout(v, 1)
        self.spin = QLabel('⏳')
        h.addWidget(self.spin)

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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Book Forge')
        icon = os.path.join(os.path.dirname(__file__), 'icon.png')
        if os.path.exists(icon):
            self.setWindowIcon(QIcon(icon))
        self.resize(1080, 720)
        self.setMinimumSize(860, 560)
        self.L, self.D = lib.shared(), dev.shared()

        self.side = QListWidget()
        self.side.setMinimumWidth(200)
        self.side.setMaximumWidth(300)
        self.side.setIconSize(QSize(18, 18))

        def section(text):
            it = QListWidgetItem(text)
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            f = it.font()
            f.setBold(True)
            f.setPointSizeF(f.pointSizeF() * 0.85)
            it.setFont(f)
            self.side.addItem(it)
        section('Library')
        self.all_item = QListWidgetItem('📚  All Books')
        self.side.addItem(self.all_item)
        section('Devices')
        self.dev_item = QListWidgetItem()
        self.dev_row = DeviceRow()
        self.dev_item.setSizeHint(QSize(200, 44))
        self.side.addItem(self.dev_item)
        self.side.setItemWidget(self.dev_item, self.dev_row)
        section('Tools')
        self.forge_item = QListWidgetItem('🪄  Convert & Translate')
        self.side.addItem(self.forge_item)

        self.stack = QStackedWidget()
        self.library = LibraryView(self)
        self.device = DeviceView(self)
        self.forge = ForgeView(self)
        for w in (self.library, self.device, self.forge):
            self.stack.addWidget(w)
        self.side.currentItemChanged.connect(self.select)
        self.side.setCurrentItem(self.all_item)

        split = QSplitter()
        split.addWidget(self.side)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 860])
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)

        file_menu = self.menuBar().addMenu('&File')
        file_menu.addAction('Add Books…', self.library.choose)
        file_menu.addSeparator()
        file_menu.addAction('Quit', QApplication.instance().quit)
        help_menu = self.menuBar().addMenu('&Help')
        help_menu.addAction('About Book Forge', lambda: QMessageBox.about(
            self, 'About Book Forge', 'Book Forge %s\n\nYour e-book library, PDF to EPUB, translation, and Kindle sync.\n'
            'Uses Calibre for e-readers and file conversion.' % VERSION))

        self.L.changed.connect(self.refresh)
        self.D.changed.connect(self.refresh)
        self.D.ask_quit_calibre.connect(self.ask_quit_calibre)
        self.refresh()

    def select(self, item, _=None):
        if item is self.all_item:
            self.stack.setCurrentWidget(self.library)
        elif item is self.dev_item:
            self.stack.setCurrentWidget(self.device)
        elif item is self.forge_item:
            self.stack.setCurrentWidget(self.forge)

    def show_device(self):
        self.side.setCurrentItem(self.dev_item)

    def translate(self, book):
        self.forge.load(self.L.url(book), book)
        self.side.setCurrentItem(self.forge_item)

    def refresh(self):
        self.all_item.setText('📚  All Books  (%d)' % len(self.L.books))
        self.dev_row.refresh()

    def ask_quit_calibre(self):
        r = QMessageBox.question(self, 'Close Calibre?', "The Calibre app is open and holds the e-reader connection. "
                                 "It has to be closed for Book Forge to change what's on the reader.",
                                 QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Ok:
            self.D.quit_calibre_and_continue()
        else:
            self.D.cancel_pending()
