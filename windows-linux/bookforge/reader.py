"""The built-in reader: EPUBs turn pages like an e-reader in a web view, PDFs show page by page."""
import json, os, re, shutil, zipfile
from urllib.parse import unquote

from lxml import etree
from .qt import (QAction, QActionGroup, QApplication, QColor, QHBoxLayout, QKeySequence, QLabel, QListWidget,
                     QListWidgetItem, QMainWindow, QMenu, QPalette, QPixmap, QScrollArea, QShortcut, QSize, QSlider, Qt,
                     QTimer, QToolBar, QToolButton, QUrl, QVBoxLayout, QWidget, QDesktopServices, QSpinBox, QDialog)
from .qt import QWebEnginePage, QWebEngineScript, QWebEngineView

from . import library as lib
from .util import Failure, cache_dir, in_background, settings

THEMES = {'light': ('#ffffff', '#1d1d1f'), 'sepia': ('#f4ecd8', '#433422'), 'dark': ('#1e1e1e', '#d9d9d9')}


class Package:
    """An EPUB unpacked into the cache, with its reading order and table of contents."""

    def __init__(self, epub, id):
        root = os.path.join(cache_dir(), 'Reader', id)
        stamp = os.path.join(root, '.bookforge-stamp')
        mtime = str(os.path.getmtime(epub))
        try:
            with open(stamp) as f:
                fresh = f.read() == mtime
        except OSError:
            fresh = False
        if not fresh:
            shutil.rmtree(root, ignore_errors=True)
            os.makedirs(root)
            with zipfile.ZipFile(epub) as z:
                z.extractall(root)
                opf_rel = lib.opf_path(z)
            with open(stamp, 'w') as f:
                f.write(mtime)
        else:
            with zipfile.ZipFile(epub) as z:
                opf_rel = lib.opf_path(z)
        if not opf_rel:
            raise Failure('This EPUB has no package file.')
        self.root = root
        parser = etree.XMLParser(recover=True, resolve_entities=False)
        opf_file = os.path.join(root, opf_rel)
        opf = etree.parse(opf_file, parser)
        opf_dir = os.path.dirname(opf_file)

        def file(href, d):
            return os.path.normpath(os.path.join(d, unquote(href.split('#')[0])))
        manifest = {}
        for item in opf.xpath("//*[local-name()='manifest']/*[local-name()='item']"):
            manifest[item.get('id')] = (file(item.get('href') or '', opf_dir), item.get('properties') or '', item.get('media-type') or '')
        spine_el = next(iter(opf.xpath("//*[local-name()='spine']")), None)
        self.spine = [manifest[r.get('idref')][0] for r in opf.xpath("//*[local-name()='spine']/*[local-name()='itemref']")
                      if r.get('idref') in manifest and os.path.exists(manifest[r.get('idref')][0])]
        if not self.spine:
            raise Failure('This EPUB has no pages.')
        self.rtl = spine_el is not None and spine_el.get('page-progression-direction') == 'rtl'
        sizes = [max(1, os.path.getsize(p)) for p in self.spine]
        total = sum(sizes)
        self.starts, acc = [], 0
        for s in sizes:
            self.starts.append(acc / total)
            acc += s
        self.shares = [s / total for s in sizes]

        # table of contents: EPUB 3 nav, else the EPUB 2 NCX
        self.toc = []       # (title, chapter, anchor, depth)

        def add(title, href, base, depth):
            t = re.sub(r'\s+', ' ', title or '').strip()
            target = file(href, base)
            if not t or target not in self.spine:
                return
            self.toc.append((t, self.spine.index(target), href.split('#', 1)[1] if '#' in href else None, depth))
        nav = next((v for v in manifest.values() if 'nav' in v[1].split()), None)
        if nav and os.path.exists(nav[0]):
            try:
                doc = etree.parse(nav[0], parser)
                navs = doc.xpath("//*[local-name()='nav']")
                toc_nav = next((n for n in navs if any(k.endswith('type') and v == 'toc' for k, v in n.attrib.items())),
                               navs[0] if navs else None)
                if toc_nav is not None:
                    for a in toc_nav.xpath(".//*[local-name()='a']"):
                        depth = sum(1 for p in a.iterancestors() if etree.QName(p).localname == 'ol') - 1
                        add(''.join(a.itertext()), a.get('href') or '', os.path.dirname(nav[0]), max(0, depth))
            except Exception:
                pass
        if not self.toc:
            ncx = manifest.get(spine_el.get('toc')) if spine_el is not None else None
            ncx = ncx or next((v for v in manifest.values() if v[2] == 'application/x-dtbncx+xml'), None)
            if ncx and os.path.exists(ncx[0]):
                try:
                    doc = etree.parse(ncx[0], parser)
                    for p in doc.xpath("//*[local-name()='navPoint']"):
                        title = ''.join(p.xpath("./*[local-name()='navLabel']/*[local-name()='text']//text()"))
                        src = next(iter(p.xpath("./*[local-name()='content']/@src")), '')
                        depth = sum(1 for a in p.iterancestors() if etree.QName(a).localname == 'navPoint')
                        add(title, src, os.path.dirname(ncx[0]), depth)
                except Exception:
                    pass


class Page(QWebEnginePage):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message.startswith('BF'):
            try:
                self.owner.on_message(json.loads(message[2:]))
            except ValueError:
                pass

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            QTimer.singleShot(0, lambda: self.owner.follow(url))
            return False
        return True


def system_dark():
    return QApplication.instance().palette().color(QPalette.ColorRole.Window).lightness() < 128


class EPUBReader(QMainWindow):
    def __init__(self, book, pkg):
        super().__init__()
        self.book, self.pkg = book, pkg
        self.chapter, self.page, self.pages = 0, 0, 1
        self.loading = True
        self.landing = (0, None)
        self.setWindowTitle('%s — %s' % (book['title'], book['author']) if book['author'] else book['title'])
        self.resize(900, 820)
        self.setMinimumSize(480, 480)

        with open(os.path.join(os.path.dirname(__file__), 'reader.js'), encoding='utf-8') as f:
            js = f.read()
        self.view = QWebEngineView(self)
        self.view.setPage(Page(self))
        script = QWebEngineScript()
        script.setSourceCode(js)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        self.view.page().scripts().insert(script)
        self.view.loadFinished.connect(self.loaded)

        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        self.prev_b = QToolButton(text='‹')
        self.prev_b.clicked.connect(self.previous)
        self.title_l = QLabel(book['title'])
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setMaximumWidth(220)
        self.slider.sliderReleased.connect(lambda: self.go_progress(self.slider.value() / 1000))
        self.slider.sliderMoved.connect(lambda v: self.pct_l.setText('%d%%' % round(v / 10)))
        self.pct_l = QLabel('0%')
        self.page_l = QLabel('')
        self.next_b = QToolButton(text='›')
        self.next_b.clicked.connect(self.next)
        for w in (self.prev_b, self.title_l):
            h.addWidget(w)
        h.addStretch(1)
        for w in (self.slider, self.pct_l, self.page_l, self.next_b):
            h.addWidget(w)
        self.bar = bar
        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.view, 1)
        v.addWidget(bar)
        self.setCentralWidget(central)

        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        contents = tb.addAction('Contents')
        contents.triggered.connect(self.show_contents)
        contents.setEnabled(bool(pkg.toc))
        text_b = QToolButton(text='Text')
        text_b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        text_b.setMenu(self.text_menu())
        tb.addWidget(text_b)

        self._save = QTimer(self, singleShot=True, interval=1000)
        self._save.timeout.connect(self.save_position)
        pos = book.get('position') or {}
        self.load(min(pos.get('chapter', 0), len(pkg.spine) - 1), pos.get('fraction', 0))

    # MARK: settings

    def settings(self):
        theme = settings.get('readerTheme', 'auto')
        if theme == 'auto':
            theme = 'dark' if system_dark() else 'light'
        return dict(scale=settings.get('readerScale', 100), theme=theme,
                    font=settings.get('readerFont', 'book'), spread=settings.get('readerSpread', True))

    def apply_theme(self):
        bg, fg = THEMES[self.settings()['theme']]
        self.view.page().setBackgroundColor(QColor(bg))
        self.bar.setStyleSheet('background: %s; color: %s;' % (bg, fg))

    def restyle(self):
        self.apply_theme()
        self.js('bf.restyle(%s)' % json.dumps(self.settings()))

    def text_menu(self):
        m = QMenu(self)

        def change(key, value):
            settings.set(key, value)
            self.restyle()
        bigger = m.addAction('Bigger Text')
        bigger.setShortcut(QKeySequence.StandardKey.ZoomIn)
        bigger.triggered.connect(lambda: change('readerScale', min(250, settings.get('readerScale', 100) + 10)))
        smaller = m.addAction('Smaller Text')
        smaller.setShortcut(QKeySequence.StandardKey.ZoomOut)
        smaller.triggered.connect(lambda: change('readerScale', max(60, settings.get('readerScale', 100) - 10)))
        self.addAction(bigger)
        self.addAction(smaller)
        m.addSeparator()
        for key, label, options in (('readerTheme', 'Theme', [('auto', 'Auto'), ('light', 'Light'), ('sepia', 'Sepia'), ('dark', 'Dark')]),
                                    ('readerFont', 'Font', [('book', "Book's own"), ('serif', 'Serif'), ('sans', 'Sans serif')])):
            m.addSection(label)
            g = QActionGroup(m)
            current = settings.get(key, options[0][0])
            for value, text in options:
                a = m.addAction(text)
                a.setCheckable(True)
                a.setChecked(value == current)
                g.addAction(a)
                a.triggered.connect(lambda _=False, k=key, v=value: change(k, v))
        m.addSeparator()
        spread = m.addAction('Two Pages in Wide Windows')
        spread.setCheckable(True)
        spread.setChecked(settings.get('readerSpread', True))
        spread.toggled.connect(lambda on: change('readerSpread', on))
        return m

    # MARK: moving around

    def js(self, code):
        self.view.page().runJavaScript(code)

    def load(self, index, fraction=0, anchor=None):
        if not 0 <= index < len(self.pkg.spine):
            return
        self.chapter = index
        self.landing = (fraction, anchor)
        self.loading = True
        self.apply_theme()
        self.view.load(QUrl.fromLocalFile(self.pkg.spine[index]))

    def loaded(self, ok):
        fraction, anchor = self.landing
        self.js('bf.setup(%s, %s, %s, %s)' % (json.dumps(self.settings()), fraction,
                                              json.dumps(anchor), 'true' if self.pkg.rtl else 'false'))

    def next(self):
        self.js('bf.next()')

    def previous(self):
        self.js('bf.prev()')

    @property
    def progress(self):
        f = self.page / (self.pages - 1) if self.pages > 1 else 0
        return min(1, self.pkg.starts[self.chapter] + self.pkg.shares[self.chapter] * f)

    def current_entry(self):
        return next((e for e in reversed(self.pkg.toc) if e[1] <= self.chapter), None)

    def go_progress(self, p):
        i = max([k for k, s in enumerate(self.pkg.starts) if s <= p] or [0])
        f = (p - self.pkg.starts[i]) / self.pkg.shares[i] if self.pkg.shares[i] > 0 else 0
        f = min(1, max(0, f))
        if i == self.chapter:
            self.js('bf.fraction(%s)' % f)
        else:
            self.load(i, f)

    def go_entry(self, e):
        _, ch, anchor, _ = e
        if ch == self.chapter and anchor:
            self.js('bf.anchor(%s)' % json.dumps(anchor))
        else:
            self.load(ch, 0, anchor)

    def follow(self, url):
        if url.isLocalFile():
            target = os.path.normpath(url.toLocalFile())
            if target in self.pkg.spine:
                i = self.pkg.spine.index(target)
                frag = url.fragment() or None
                if i == self.chapter and frag:
                    self.js('bf.anchor(%s)' % json.dumps(frag))
                else:
                    self.load(i, 0, frag)
        else:
            QDesktopServices.openUrl(url)

    def on_message(self, m):
        if m.get('type') == 'page':
            self.page = m.get('page', 0)
            self.pages = max(1, m.get('pages', 1))
            if self.loading:
                self.loading = False
                self.view.setFocus()
            e = self.current_entry()
            self.title_l.setText(e[0] if e else self.book['title'])
            if not self.slider.isSliderDown():
                self.slider.setValue(int(self.progress * 1000))
                self.pct_l.setText('%d%%' % round(self.progress * 100))
            self.page_l.setText('Page %d of %d' % (self.page + 1, self.pages))
            self._save.start()
        elif m.get('type') == 'edge':
            if m.get('dir') == 'next':
                if self.chapter + 1 < len(self.pkg.spine):
                    self.load(self.chapter + 1)
            elif self.chapter > 0:
                self.load(self.chapter - 1, 1)

    def save_position(self):
        f = self.page / (self.pages - 1) if self.pages > 1 else 0
        lib.shared().set_position(self.book['id'], self.chapter, f, self.progress)

    def closeEvent(self, e):
        self.save_position()
        super().closeEvent(e)

    def show_contents(self):
        d = QDialog(self)
        d.setWindowTitle('Contents')
        d.resize(340, 440)
        lst = QListWidget(d)
        QVBoxLayout(d).addWidget(lst)
        cur = self.current_entry()
        for e in self.pkg.toc:
            it = QListWidgetItem('    ' * e[3] + e[0])
            if e == cur:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            lst.addItem(it)
        if cur:
            lst.setCurrentRow(self.pkg.toc.index(cur))

        def pick(item):
            self.go_entry(self.pkg.toc[lst.row(item)])
            d.accept()
        lst.itemActivated.connect(pick)
        lst.itemClicked.connect(pick)
        d.exec()


class PDFReader(QMainWindow):
    """A PDF page by page, rendered with pdfium."""

    def __init__(self, book):
        super().__init__()
        from .pdfread import page_count
        self.book = book
        self.path = lib.shared().url(book)
        self.count = max(1, page_count(self.path))
        self.index = min((book.get('position') or {}).get('chapter', 0), self.count - 1)
        self.setWindowTitle(book['title'])
        self.resize(900, 1000)
        self.label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet('background: #808080;')
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.label)
        self.scroll.setWidgetResizable(True)
        self.spin = QSpinBox()
        self.spin.setRange(1, self.count)
        self.spin.valueChanged.connect(lambda v: self.show_page(v - 1))
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        prev = QToolButton(text='‹')
        prev.clicked.connect(lambda: self.show_page(self.index - 1))
        nxt = QToolButton(text='›')
        nxt.clicked.connect(lambda: self.show_page(self.index + 1))
        h.addWidget(prev)
        h.addStretch(1)
        h.addWidget(QLabel('Page'))
        h.addWidget(self.spin)
        h.addWidget(QLabel('of %d' % self.count))
        h.addStretch(1)
        h.addWidget(nxt)
        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.scroll, 1)
        v.addWidget(bar)
        self.setCentralWidget(central)
        for key, step in ((Qt.Key.Key_Right, 1), (Qt.Key.Key_PageDown, 1), (Qt.Key.Key_Space, 1),
                          (Qt.Key.Key_Left, -1), (Qt.Key.Key_PageUp, -1)):
            QShortcut(QKeySequence(key), self, lambda s=step: self.show_page(self.index + s))
        self._token = 0
        QTimer.singleShot(0, lambda: self.show_page(self.index))

    def show_page(self, i):
        if not 0 <= i < self.count:
            return
        self.index = i
        if self.spin.value() != i + 1:
            self.spin.blockSignals(True)
            self.spin.setValue(i + 1)
            self.spin.blockSignals(False)
        self._token += 1
        token = self._token
        width = max(400, self.scroll.viewport().width() - 20)
        dpr = self.devicePixelRatioF()
        path = self.path

        def render():
            return lib.render_page(path, i, width=int(width * dpr), fmt='PNG')

        def show(data):
            if token != self._token or not data:
                return
            pm = QPixmap()
            pm.loadFromData(data)
            pm.setDevicePixelRatio(dpr)
            self.label.setPixmap(pm)
            self.scroll.verticalScrollBar().setValue(0)
            lib.shared().set_position(self.book['id'], i, 0, i / (self.count - 1) if self.count > 1 else 1)
        in_background(render, show)


_open = {}


def open_book(book, parent=None):
    """Opens a reader window for a library book (or brings its window forward)."""
    w = _open.get(book['id'])
    if w is not None and w.isVisible():
        w.raise_()
        w.activateWindow()
        return
    L = lib.shared()
    if L.format(book) == 'pdf':
        w = PDFReader(book)
    else:
        w = EPUBReader(book, Package(L.url(book), book['id']))
    _open[book['id']] = w
    w.show()
