"""The e-reader screen: what's on it, one-click Import and Send, and tick-to-sync."""
from .qt import (QBrush, QColor, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton, QSize,
                     QStackedWidget, QStyledItemDelegate, Qt, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import devices as dev, theme
from . import library as lib
from .deviceart import DeviceArt
from .util import calibre_app_running, human_readable

ROLE = Qt.ItemDataRole.UserRole


def rewire(button, slot):
    """Points a button at a new action (PySide only warns when there was nothing to disconnect)."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        try:
            button.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
    if slot:
        button.clicked.connect(slot)


def big(label, scale=1.6, bold=True):
    f = label.font()
    f.setPointSizeF(f.pointSizeF() * scale)
    f.setBold(bold)
    label.setFont(f)
    return label


class RoomyRows(QStyledItemDelegate):
    """Taller rows, as in the Mac's lists (padding in the style sheet would push the row buttons off centre)."""
    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        return QSize(s.width(), max(s.height() + 16, 38))


class DeviceView(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.L, self.D = lib.shared(), dev.shared()
        self.wanted, self.keep = set(), set()       # library ids to have on the reader; reader-only paths to keep
        self.known_ids = set()
        self.reader_key = None
        self.building = False

        self.stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.head = theme.Header('E-reader')
        self.filter = theme.search_field('Filter books')
        self.filter.setFixedWidth(300)
        self.filter.textChanged.connect(self.rebuild)
        self.head.add(self.filter)
        outer.addWidget(self.head)
        outer.addWidget(self.stack)

        # connected
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        header = QWidget()
        hh = QHBoxLayout(header)
        hh.setContentsMargins(28, 24, 24, 20)
        hh.setSpacing(32)
        self.art = DeviceArt('generic', (130, 170))
        hh.addWidget(self.art, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(6)
        self.name_l = big(QLabel(), 2.0)
        self.family_l = QLabel()
        self.gauge = QProgressBar()
        self.gauge.setTextVisible(False)
        self.gauge.setMaximumWidth(280)
        self.gauge.setFixedHeight(10)
        self.gauge.setProperty('gauge', True)
        self.free_l = QLabel()
        self.compare_l = QLabel()
        for w in (self.family_l, self.free_l, self.compare_l):
            w.setProperty('secondary', True)
        for w in (self.name_l, self.family_l, self.gauge, self.free_l, self.compare_l):
            info.addWidget(w)
        row = QHBoxLayout()
        self.import_b = QPushButton()
        self.import_b.setProperty('iconName', 'import')
        self.import_b.clicked.connect(lambda: self.act(self.D.import_new))
        self.send_b = QPushButton()
        self.send_b.setProperty('iconName', 'send')
        self.send_b.clicked.connect(lambda: self.act(self.D.send_new))
        for b in (self.import_b, self.send_b):
            b.setMinimumHeight(36)
            row.addWidget(b)
        row.addStretch(1)
        info.addLayout(row)
        row2 = QHBoxLayout()
        self.refresh_b = QPushButton('Refresh')
        self.refresh_b.setProperty('iconName', 'refresh')
        self.refresh_b.clicked.connect(lambda: self.D.scan())
        self.eject_b = QPushButton('Eject')
        self.eject_b.setProperty('iconName', 'eject')
        self.eject_b.clicked.connect(lambda: self.D.perform(self.D.eject))
        row2.addWidget(self.refresh_b)
        row2.addWidget(self.eject_b)
        row2.addStretch(1)
        info.addLayout(row2)
        hh.addLayout(info, 1)
        v.addWidget(header)

        self.tree = QTreeWidget()
        self.tree.setObjectName('books')
        self.tree.setItemDelegate(RoomyRows(self.tree))
        self.tree.setIndentation(0)
        self.tree.setColumnCount(4)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            self.tree.header().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemChanged.connect(self.ticked)
        self.tree.header().setMinimumSectionSize(72)
        tw = QWidget()
        tl = QVBoxLayout(tw)
        tl.setContentsMargins(18, 0, 18, 0)
        tl.addWidget(self.tree)
        v.addWidget(tw, 1)

        footer = QWidget()
        fh = QHBoxLayout(footer)
        fh.setContentsMargins(24, 10, 20, 12)
        self.footer_l = QLabel(wordWrap=True)
        self.footer_l.setProperty('secondary', True)
        self.revert_b = QPushButton('Revert')
        self.revert_b.clicked.connect(self.reset)
        self.sync_b = QPushButton('Sync')
        self.sync_b.setDefault(True)
        self.sync_b.clicked.connect(self.sync)
        fh.addWidget(self.footer_l, 1)
        fh.addWidget(self.revert_b)
        fh.addWidget(self.sync_b)
        v.addWidget(footer)
        self.stack.addWidget(page)

        # not connected
        self.empty = QWidget()
        ev = QVBoxLayout(self.empty)
        ev.addStretch(1)
        self.empty_art = DeviceArt('generic', (60, 80))
        ev.addWidget(self.empty_art, 0, Qt.AlignmentFlag.AlignCenter)
        self.empty_title = big(QLabel(alignment=Qt.AlignmentFlag.AlignCenter), 1.3)
        self.empty_text = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.empty_text.setProperty('secondary', True)
        self.empty_text.setFixedWidth(480)      # a fixed width lets the wrapped text get its full height
        ev.addWidget(self.empty_title)
        # centred by stretches, not an alignment flag: with a flag the layout ignores the wrapped text's height
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.empty_text)
        row.addStretch(1)
        ev.addLayout(row)
        br = QHBoxLayout()
        br.addStretch(1)
        self.empty_b1 = QPushButton()
        self.empty_b2 = QPushButton('Check Again')
        self.empty_b2.clicked.connect(lambda: self.D.scan())
        br.addWidget(self.empty_b1)
        br.addWidget(self.empty_b2)
        br.addStretch(1)
        ev.addLayout(br)
        ev.addStretch(1)
        self.stack.addWidget(self.empty)

        self.D.changed.connect(self.refresh)
        self.L.changed.connect(self.library_changed)
        self.refresh()

    def act(self, fn):
        self.D.message = None
        self.D.perform(fn)

    # MARK: state

    def reset(self):
        self.wanted = {b['id'] for b in self.L.books if self.D.match(b)}
        self.keep = {b['path'] for b in self.D.only_on_reader()}
        self.known_ids = {b['id'] for b in self.L.books}
        self.rebuild()

    def library_changed(self):
        # books that just came into the library (imported from the reader, say) and are on it stay ticked
        ids = {b['id'] for b in self.L.books}
        fresh = ids - self.known_ids
        self.wanted |= {b['id'] for b in self.L.books if b['id'] in fresh and self.D.match(b)}
        self.known_ids = ids
        self.refresh()

    def to_add(self):
        return [b for b in self.L.books if b['id'] in self.wanted and not self.D.match(b)]

    def to_remove(self):
        return ([c for b in self.L.books if b['id'] not in self.wanted for c in self.D.copies(b)]
                + [d for d in self.D.only_on_reader() if d['path'] not in self.keep])

    def refresh(self):
        r = self.D.reader
        if self.D.state == 'connected' and r:
            key = (r['name'], tuple(b['path'] for b in r['books']))
            if key != self.reader_key:
                self.reader_key = key
                self.reset()
            self.stack.setCurrentIndex(0)
            self.head.title.setText(dev.short_name(r))
            self.filter.show()
            self.fill_header(r)
            self.rebuild()
        else:
            self.reader_key = None
            self.stack.setCurrentIndex(1)
            self.head.title.setText('E-reader')
            self.filter.hide()
            self.fill_empty()

    def fill_header(self, r):
        s = dev.short_name(r)
        cover = next((self.L.cover(b) for b in self.L.books if self.D.match(b) and self.L.cover(b)), None)
        self.art.set(r['kind'], cover)
        self.name_l.setText(r['name'])
        self.family_l.setText('%s · connected by USB' % dev.FAMILY.get(r['kind'], 'E-reader'))
        used = max(0, r['total'] - r['free'])
        self.gauge.setVisible(r['total'] > 0)
        self.gauge.setRange(0, 1000)
        self.gauge.setValue(int(1000 * used / r['total']) if r['total'] else 0)
        self.free_l.setText('%s free of %s · %d books' % (human_readable(r['free']), human_readable(r['total']), len(r['books'])))
        both = sum(1 for b in self.L.books if self.D.match(b))
        self.compare_l.setText('%d in both · %d only in your library · %d only on the %s'
                               % (both, len(self.L.books) - both, len(self.D.only_on_reader()), s))
        n, m = len(self.D.to_import()), len(self.D.to_send())
        self.import_b.setText(('Nothing New to Import' if n == 0 else 'Import %d New Book%s' % (n, '' if n == 1 else 's')))
        self.import_b.setToolTip('Copy the books that are only on the %s into your library. Books already in it, '
                                 'extra copies and store books are skipped.' % s)
        self.send_b.setText(('Nothing New to Send' if m == 0 else 'Send %d New Book%s' % (m, '' if m == 1 else 's')))
        self.send_b.setToolTip("Copy the library books that aren't on the %s yet. Nothing is removed and nothing is copied twice." % s)
        idle = self.D.working is None and not self.D.scanning
        self.import_b.setEnabled(idle and n > 0)
        self.send_b.setEnabled(idle and m > 0)
        self.refresh_b.setEnabled(idle)
        self.eject_b.setEnabled(idle)

    def tag(self, item, col, text, color):
        item.setText(col, text)
        item.setForeground(col, QBrush(QColor(color)))
        f = item.font(col)
        f.setPointSizeF(f.pointSizeF() * 0.9)
        f.setBold(True)
        item.setFont(col, f)

    def rebuild(self):
        r = self.D.reader
        if not r:
            return
        s = dev.short_name(r)
        q = self.filter.text().strip().casefold()
        self.building = True
        self.tree.clear()
        mine = QTreeWidgetItem(['Your library: tick the books to keep on the %s' % s])
        mine.setFlags(Qt.ItemFlag.ItemIsEnabled)
        f = mine.font(0)
        f.setBold(True)
        mine.setFont(0, f)
        self.tree.addTopLevelItem(mine)
        shown = sorted((b for b in self.L.books if not q or q in b['title'].casefold() or q in b['author'].casefold()),
                       key=lambda b: b['title'].casefold())
        for b in shown:
            on = self.D.match(b) is not None
            want = b['id'] in self.wanted
            it = QTreeWidgetItem(mine, [b['title'] + ('  —  ' + b['author'] if b['author'] else '')])
            it.setData(0, ROLE, ('lib', b['id']))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Checked if want else Qt.CheckState.Unchecked)
            if on and want:
                self.tag(it, 1, 'On %s' % s, '#34c759')
            elif want:
                self.tag(it, 1, 'Will be added (as Kindle book)' if dev.is_kindle(r) and lib.Library.format(b) == 'epub'
                         else 'Will be added', theme.C['accent'])
            elif on:
                self.tag(it, 1, 'Will be removed', '#ff3b30')
            else:
                self.tag(it, 1, 'Not on %s' % s, theme.C['secondary'])
        mine.setExpanded(True)

        orphans = [d for d in self.D.only_on_reader() if not q or q in dev.display_title(d).casefold()]
        if orphans:
            only = QTreeWidgetItem(['Only on the %s (not in your library)' % s])
            only.setFlags(Qt.ItemFlag.ItemIsEnabled)
            only.setFont(0, f)
            self.tree.addTopLevelItem(only)
            if self.D.to_import():
                link = QPushButton('Import All')
                link.setFlat(True)
                link.setEnabled(self.D.working is None)
                link.clicked.connect(lambda: self.act(self.D.import_new))
                self.tree.setItemWidget(only, 3, link)
            for d in orphans:
                it = QTreeWidgetItem(only, [dev.display_title(d) + ('  —  ' + ', '.join(d['authors']) if d.get('authors') else '')])
                it.setData(0, ROLE, ('dev', d['path']))
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Checked if d['path'] in self.keep else Qt.CheckState.Unchecked)
                if d['path'] not in self.keep:
                    self.tag(it, 1, 'Will be removed', '#ff3b30')
                elif self.D.likely_locked(d):
                    self.tag(it, 1, 'Store book', theme.C['secondary'])
                    it.setToolTip(1, 'Bought from the Kindle Store and locked with DRM, so Import All leaves it on the reader'
                                  if dev.importable(d) else "Bought from the Kindle Store in Amazon's KFX format, which only a Kindle can open")
                else:
                    self.tag(it, 1, 'Not in library', '#ff9500')
                it.setText(2, human_readable(d.get('size') or 0))
                if dev.importable(d):
                    b = QPushButton('Import')
                    b.setFlat(True)
                    b.setEnabled(self.D.working is None)
                    b.setToolTip('Try copying this book into your library (store books usually can\'t be)'
                                 if self.D.likely_locked(d) else 'Copy this book into your library')
                    b.clicked.connect(lambda _=False, book=d: self.act(lambda: self.D.import_books([book])))
                    self.tree.setItemWidget(it, 3, b)
            only.setExpanded(True)
        self.building = False
        self.fill_footer()

    def ticked(self, item, col):
        if self.building or col != 0:
            return
        data = item.data(0, ROLE)
        if not data:
            return
        on = item.checkState(0) == Qt.CheckState.Checked
        kind, k = data
        target = self.wanted if kind == 'lib' else self.keep
        (target.add if on else target.discard)(k)
        self.rebuild()

    def fill_footer(self):
        add, remove = self.to_add(), self.to_remove()
        parts = []
        if add:
            parts.append('%d to add' % len(add))
        if remove:
            parts.append('%d to remove' % len(remove))
        if self.D.working:
            text = '⏳ ' + self.D.working
        elif self.D.message:
            text = self.D.message
        else:
            text = ' · '.join(parts) if parts else 'Everything ticked is on the reader.'
        self.footer_l.setText(text)
        idle = self.D.working is None
        self.revert_b.setEnabled(bool(parts) and idle)
        self.sync_b.setEnabled(bool(parts) and idle)

    def sync(self):
        add, remove = self.to_add(), self.to_remove()
        self.act(lambda: self.D.sync(add, remove))

    def fill_empty(self):
        st = self.D.state
        self.empty_b1.hide()
        rewire(self.empty_b1, None)
        self.empty_b2.setText('Check Again')
        if st == 'busy':
            name = self.D.state_name
            self.empty_art.set(self.D.busy_kind)
            self.empty_title.setText(name)
            if calibre_app_running():
                self.empty_text.setText('The Calibre app is using it. Close Calibre so Book Forge can reach the reader.')
                self.empty_b1.setText('Quit Calibre')
                rewire(self.empty_b1, lambda: self.D.perform(self.D._rescan_blocking))
                self.empty_b1.show()
            else:
                self.empty_text.setText('Another app is using it. Close that app, replug the reader and try again.')
        elif st == 'failed':
            self.empty_art.set('generic', dim=True)
            self.empty_title.setText("Couldn't read the e-reader")
            self.empty_text.setText(self.D.state_name)
            self.empty_b2.setText('Try Again')
        elif st == 'ejected':
            self.empty_art.set(self.D.busy_kind)
            self.empty_title.setText(self.D.state_name)
            self.empty_text.setText("It's safe to unplug your %s." % self.D.state_name)
            self.empty_b2.setText('Connect Again')
            rewire(self.empty_b2, self.connect_again)
            return
        else:
            self.empty_art.set('generic', dim=True)
            self.empty_title.setText('No e-reader connected')
            self.empty_text.setText('Connect your Kindle, Kobo, PocketBook or other e-reader to this computer with a USB '
                                    'data cable and unlock it. It shows up here by itself.')
        rewire(self.empty_b2, lambda: self.D.scan())
        self.empty_b2.setEnabled(not self.D.scanning)

    def connect_again(self):
        self.D.state = 'none'
        self.D.scan()
