"""The library grid: covers, reading progress, which books are on the e-reader, and one-click Import/Send."""
import os

from qt.core import (QAbstractItemView, QAction, QActionGroup, QBrush, QColor, QDialog, QDialogButtonBox, QFileDialog,
                     QFont, QFontMetrics, QFormLayout, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                     QListWidgetItem, QMenu, QMessageBox, QPainter, QPainterPath, QPen, QPushButton, QRectF, QSize,
                     QStyle, QStyledItemDelegate, Qt, QPointF, QToolButton, QVBoxLayout, QWidget, QLinearGradient, QPalette)

from . import devices as dev
from . import library as lib
from .util import reveal, settings

ID = Qt.ItemDataRole.UserRole


def hue(title):
    """Stable across launches, unlike hash()."""
    h = 0
    for c in title:
        h = (h * 31 + ord(c)) & 0xffff
    return (h % 360) / 360


def paint_cover(p, rect, book, cover):
    """A 2:3 frame the cover fills and is cropped to; a drawn cover when the book has none."""
    path = QPainterPath()
    path.addRoundedRect(rect, 4, 4)
    p.save()
    p.setClipPath(path)
    if cover is not None:
        scaled = cover.scaled(rect.size().toSize() * 2, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
        sx = (scaled.width() - rect.width() * 2) / 2
        sy = (scaled.height() - rect.height() * 2) / 2
        p.drawPixmap(rect, scaled, QRectF(sx, sy, rect.width() * 2, rect.height() * 2))
    else:
        g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g.setColorAt(0, QColor.fromHsvF(hue(book['title']), 0.45, 0.55))
        g.setColorAt(1, QColor.fromHsvF(hue(book['title']), 0.55, 0.35))
        p.fillRect(rect, QBrush(g))
        p.setPen(QColor('white'))
        f = QFont('serif')
        f.setPointSizeF(max(8, rect.width() / 12))
        f.setBold(True)
        p.setFont(f)
        p.drawText(rect.adjusted(8, 8, -8, -8), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                   book['title'] + ('\n\n' + book['author'] if book['author'] else ''))
    p.restore()


class CoverDelegate(QStyledItemDelegate):
    W, H = 150, 290

    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index):
        return QSize(self.W, self.H)

    def paint(self, p, option, index):
        book = lib.shared().book(index.data(ID))
        if not book:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(6, 6, -6, -6)
        if option.state & QStyle.StateFlag.State_Selected:
            c = option.palette.color(QPalette.ColorRole.Highlight)
            c.setAlphaF(0.25)
            p.setBrush(c)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(option.rect).adjusted(1, 1, -1, -1), 8, 8)
        cw = r.width() - 6
        cover_rect = QRectF(r.left() + 3, r.top() + 3, cw, cw * 1.5)
        # shadow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawRoundedRect(cover_rect.translated(0, 2), 4, 4)
        paint_cover(p, cover_rect, book, lib.shared().cover(book))
        status = self.view.status(book)
        if status:
            d = 22
            tick = QRectF(cover_rect.right() - d - 5, cover_rect.top() + 5, d, d)
            p.setBrush(QColor('#34c759') if status == 'on' else QColor('#8e8e93'))
            p.setPen(QPen(QColor('white'), 1.5))
            p.drawEllipse(tick)
            pen = QPen(QColor('white'), 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawPolyline([tick.topLeft() + QPointF(d * 0.28, d * 0.52),
                            tick.topLeft() + QPointF(d * 0.44, d * 0.68),
                            tick.topLeft() + QPointF(d * 0.72, d * 0.34)])
        if lib.Library.format(book) == 'pdf':
            badge = QRectF(cover_rect.left() + 5, cover_rect.bottom() - 21, 30, 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(badge, 3, 3)
            p.setPen(QColor('white'))
            f = QFont(option.font)
            f.setPointSizeF(f.pointSizeF() * 0.75)
            f.setBold(True)
            p.setFont(f)
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, 'PDF')
        y = cover_rect.bottom() + 6
        prog = (book.get('position') or {}).get('progress') or 0
        if prog > 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(128, 128, 128, 60))
            p.drawRoundedRect(QRectF(cover_rect.left(), y, cw, 3), 1.5, 1.5)
            p.setBrush(QColor('#34c759') if prog >= 0.99 else option.palette.color(QPalette.ColorRole.Highlight))
            p.drawRoundedRect(QRectF(cover_rect.left(), y, cw * min(1, prog), 3), 1.5, 1.5)
            y += 7
        f = QFont(option.font)
        f.setBold(True)
        p.setFont(f)
        p.setPen(option.palette.color(QPalette.ColorRole.Text))
        fm = QFontMetrics(f)
        title_rect = QRectF(cover_rect.left(), y, cw, fm.height() * 2 + 2)
        p.drawText(title_rect, Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop, self.elide2(book['title'], fm, cw))
        used = fm.boundingRect(title_rect.toRect(), Qt.TextFlag.TextWordWrap, self.elide2(book['title'], fm, cw)).height()
        f2 = QFont(option.font)
        f2.setPointSizeF(f2.pointSizeF() * 0.9)
        p.setFont(f2)
        c = option.palette.color(QPalette.ColorRole.Text)
        c.setAlphaF(0.6)
        p.setPen(c)
        p.drawText(QRectF(cover_rect.left(), y + used + 2, cw, 20), Qt.AlignmentFlag.AlignLeft,
                   QFontMetrics(f2).elidedText(book['author'], Qt.TextElideMode.ElideRight, int(cw)))
        p.restore()

    @staticmethod
    def elide2(text, fm, width):
        """Text cut to at most two lines."""
        words, lines, cur = text.split(' '), [], ''
        for w in words:
            t = (cur + ' ' + w).strip()
            if fm.horizontalAdvance(t) <= width or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= 2:
            return text
        return lines[0] + '\n' + fm.elidedText(' '.join(lines[1:]), Qt.TextElideMode.ElideRight, int(width))


class Grid(QListWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(10)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setItemDelegate(CoverDelegate(self))
        self.setGridSize(QSize(CoverDelegate.W + 12, CoverDelegate.H + 8))
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def status(self, book):
        return self.owner.status(book)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            lib.shared().add_in_background(paths)
            e.acceptProposedAction()


class EditInfo(QDialog):
    def __init__(self, book, parent):
        super().__init__(parent)
        self.setWindowTitle('Edit Info')
        self.book = dict(book)
        f = QFormLayout(self)
        self.title = QLineEdit(book['title'])
        self.author = QLineEdit(book['author'])
        self.lang = QComboBox()
        for code, name in (('id', 'Indonesian'), ('en', 'English'), ('ja', 'Japanese')):
            self.lang.addItem(name, code)
        if book['language'] not in ('id', 'en', 'ja'):
            self.lang.addItem(book['language'] or 'Unknown', book['language'])
        self.lang.setCurrentIndex(self.lang.findData(book['language']))
        f.addRow('Title', self.title)
        f.addRow('Author', self.author)
        f.addRow('Language', self.lang)
        note = QLabel("This changes how the book is listed here and named on e-readers. The book file itself isn't changed.")
        note.setWordWrap(True)
        f.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.resize(420, 0)

    def save(self):
        if not self.title.text().strip():
            return
        self.book.update(title=self.title.text().strip(), author=self.author.text().strip(), language=self.lang.currentData())
        lib.shared().update(self.book)
        self.accept()


class LibraryView(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.L, self.D = lib.shared(), dev.shared()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        top = QWidget()
        h = QHBoxLayout(top)
        h.setContentsMargins(16, 10, 16, 10)
        heading = QLabel('Library')
        f = heading.font()
        f.setPointSizeF(f.pointSizeF() * 1.5)
        f.setBold(True)
        heading.setFont(f)
        h.addWidget(heading)
        h.addStretch(1)
        self.import_b = QPushButton('Import from Kindle')
        self.import_b.clicked.connect(lambda: self.D.perform(self.D.import_new))
        self.send_b = QPushButton('Send to Kindle')
        self.send_b.clicked.connect(lambda: self.D.perform(self.D.send_new))
        h.addWidget(self.import_b)
        h.addWidget(self.send_b)
        self.sort_b = QToolButton(text='Sort ▾')
        self.sort_b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sort_menu = QMenu(self)
        self.sort_menu.aboutToShow.connect(self.fill_sort_menu)
        self.sort_b.setMenu(self.sort_menu)
        h.addWidget(self.sort_b)
        add = QPushButton('Add Books…')
        add.clicked.connect(self.choose)
        h.addWidget(add)
        self.search = QLineEdit(placeholderText='Title or author')
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(self.refresh)
        h.addWidget(self.search)
        v.addWidget(top)

        self.grid = Grid(self)
        self.grid.itemDoubleClicked.connect(lambda it: self.open(self.L.book(it.data(ID))))
        self.grid.customContextMenuRequested.connect(self.context_menu)
        v.addWidget(self.grid, 1)

        self.empty = QWidget(self.grid)
        ev = QVBoxLayout(self.empty)
        self.empty_title = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        f = self.empty_title.font()
        f.setPointSizeF(f.pointSizeF() * 1.4)
        f.setBold(True)
        self.empty_title.setFont(f)
        self.empty_text = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.empty_add = QPushButton('Add Books…')
        self.empty_add.clicked.connect(self.choose)
        ev.addStretch(1)
        ev.addWidget(self.empty_title)
        ev.addWidget(self.empty_text)
        ev.addWidget(self.empty_add, 0, Qt.AlignmentFlag.AlignCenter)
        ev.addStretch(1)

        self.status_l = QLabel(wordWrap=True)
        self.status_l.setContentsMargins(16, 8, 16, 8)
        v.addWidget(self.status_l)

        self.L.changed.connect(self.refresh)
        self.L.message_changed.connect(self.refresh_status)
        self.D.changed.connect(self.refresh)
        self.refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.empty.setGeometry(self.grid.viewport().geometry())

    # MARK: what's on the reader

    def device_name(self):
        if self.D.reader:
            return dev.short_name(self.D.reader)
        s = self.D.last_seen
        if s:
            return 'Kindle' if s['kind'].startswith('kindle') else s['name']
        return None

    def status(self, book):
        if self.D.reader:
            return 'on' if self.D.match(book) else None
        return 'was' if self.D.was_on_reader(book) else None

    def summary(self):
        r = self.D.reader
        if not r or self.D.state != 'connected':
            return None
        on = sum(1 for b in self.L.books if self.D.match(b))
        only = len(self.D.only_on_reader())
        parts = ['%d of %d books are on your %s' % (on, len(self.L.books), dev.short_name(r))]
        if only:
            parts.append('%d book%s only on the %s' % (only, ' is' if only == 1 else 's are', dev.short_name(r)))
        return ' · '.join(parts)

    def books(self):
        q = self.search.text().strip().casefold()
        found = [b for b in self.L.books if not q or q in b['title'].casefold() or q in b['author'].casefold()]
        show = settings.get('libraryShow', 'all')
        if show != 'all' and self.device_name():
            found = [b for b in found if bool(self.status(b)) == (show == 'on')]
        sort = settings.get('librarySort', 'added')
        if sort == 'title':
            found.sort(key=lambda b: b['title'].casefold())
        elif sort == 'author':
            found.sort(key=lambda b: (b['author'].casefold(), b['title'].casefold()))
        elif sort == 'recent':
            found.sort(key=lambda b: (b.get('position') is not None, b['added']), reverse=True)
        else:
            found.sort(key=lambda b: b['added'], reverse=True)
        return found

    def refresh(self):
        selected = {it.data(ID) for it in self.grid.selectedItems()}
        self.grid.clear()
        shown = self.books()
        for b in shown:
            it = QListWidgetItem()
            it.setData(ID, b['id'])
            it.setToolTip(self.tooltip(b))
            self.grid.addItem(it)
            if b['id'] in selected:
                it.setSelected(True)
        name = self.device_name()
        if not self.L.books:
            self.empty_title.setText('Your library is empty')
            self.empty_text.setText('Drag EPUB or PDF files or folders here, or click Add Books. '
                                    'Books you make in Convert & Translate are added automatically.')
            self.empty_add.show()
        elif not shown and self.search.text().strip():
            self.empty_title.setText('No results for “%s”' % self.search.text().strip())
            self.empty_text.setText('Check the spelling or try a different title or author.')
            self.empty_add.hide()
        elif not shown:
            self.empty_title.setText(('None of your books are on the %s' if settings.get('libraryShow') == 'on'
                                      else 'All your books are on the %s') % (name or 'e-reader'))
            self.empty_text.setText('')
            self.empty_add.hide()
        self.empty.setVisible(not shown)
        r = self.D.reader
        connected = r is not None and self.D.state == 'connected'
        for b, label in ((self.import_b, 'Import from %s'), (self.send_b, 'Send to %s')):
            b.setVisible(connected)
            b.setEnabled(connected and self.D.working is None)
            if connected:
                b.setText(label % dev.short_name(r))
        if connected:
            n, m = len(self.D.to_import()), len(self.D.to_send())
            s = dev.short_name(r)
            self.import_b.setToolTip('Every book on the %s you can copy is already in your library' % s if n == 0 else
                                     'Copy %d book%s that %s only on the %s into your library' % (n, '' if n == 1 else 's', 'is' if n == 1 else 'are', s))
            self.send_b.setToolTip('Every book in your library is already on the %s' % s if m == 0 else
                                   "Copy %d book%s that %s on the %s yet" % (m, '' if m == 1 else 's', "isn't" if m == 1 else "aren't", s))
        self.refresh_status()

    def tooltip(self, b):
        s = self.status(b)
        name = self.device_name() or 'e-reader'
        if s == 'on':
            return '%s\nOn your %s' % (b['title'], name)
        if s == 'was':
            return '%s\nWas on your %s when it was last connected' % (b['title'], name)
        return b['title']

    def refresh_status(self):
        text = self.D.working or self.L.message or self.D.message or self.summary()
        self.status_l.setText(text or '')
        self.status_l.setVisible(bool(text))

    def fill_sort_menu(self):
        m = self.sort_menu
        m.clear()
        m.addSection('Sort by')
        g = QActionGroup(m)
        for key, label in (('added', 'Date Added'), ('recent', 'Recently Read'), ('title', 'Title'), ('author', 'Author')):
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(settings.get('librarySort', 'added') == key)
            g.addAction(a)
            a.triggered.connect(lambda _=False, k=key: (settings.set('librarySort', k), self.refresh()))
        name = self.device_name()
        if name:
            m.addSection('Show')
            g2 = QActionGroup(m)
            for key, label in (('all', 'All Books'), ('on', 'On the %s' % name), ('off', 'Not on the %s' % name)):
                a = m.addAction(label)
                a.setCheckable(True)
                a.setChecked(settings.get('libraryShow', 'all') == key)
                g2.addAction(a)
                a.triggered.connect(lambda _=False, k=key: (settings.set('libraryShow', k), self.refresh()))

    # MARK: actions

    def choose(self):
        files, _ = QFileDialog.getOpenFileNames(self, 'Add Books', '', 'Books (*.epub *.pdf)')
        if files:
            self.L.add_in_background(files)

    def open(self, book):
        if book:
            from .reader import open_book
            try:
                open_book(book, self)
            except Exception as e:
                QMessageBox.warning(self, "Can't open this book", str(e))

    def targets(self, item):
        ids = {it.data(ID) for it in self.grid.selectedItems()}
        if item.data(ID) not in ids:
            ids = {item.data(ID)}
        return [b for b in self.L.books if b['id'] in ids]

    def context_menu(self, pos):
        item = self.grid.itemAt(pos)
        if not item:
            return
        book = self.L.book(item.data(ID))
        targets = self.targets(item)
        m = QMenu(self)
        if len(targets) == 1:
            m.addAction('Read', lambda: self.open(book))
            m.addSeparator()
        r = self.D.reader
        if r:
            missing = [b for b in targets if not self.D.match(b)]
            present = [c for b in targets for c in self.D.copies(b)]
            if missing:
                m.addAction('Send to %s' % r['name'], lambda: self.D.perform(lambda: self.D.sync(missing, [])))
            if present:
                m.addAction('Remove from %s' % r['name'], lambda: self.D.perform(lambda: self.D.sync([], present)))
        if len(targets) == 1:
            m.addAction('Email to Kindle', lambda: self.main.forge.email_book(book))
            if lib.Library.format(book) == 'epub':
                m.addAction('Translate…', lambda: self.main.translate(book))
            m.addAction('Edit Info…', lambda: EditInfo(book, self).exec())
        m.addAction('Show in Folder', lambda: reveal(self.L.url(targets[0])))
        m.addSeparator()
        m.addAction('Move to Trash…', lambda: self.trash(targets))
        m.exec(self.grid.viewport().mapToGlobal(pos))

    def trash(self, targets):
        n = len(targets)
        text = ("The book's file goes to the Trash. Copies on e-readers stay where they are." if n == 1 else
                "%d books' files go to the Trash. Copies on e-readers stay where they are." % n)
        if QMessageBox.question(self, 'Move to the Trash?', text) == QMessageBox.StandardButton.Yes:
            self.L.remove({b['id'] for b in targets})
