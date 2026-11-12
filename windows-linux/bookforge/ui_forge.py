"""Convert & Translate: PDF -> EPUB, EPUB translation with Claude, and sending to the e-reader."""
import os, smtplib, ssl
from email.message import EmailMessage

from .qt import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
                     QLabel, QLineEdit, QMessageBox, QPixmap, QProgressBar, QPushButton, QRadioButton, QScrollArea,
                     QSpinBox, QTextEdit, Qt, QToolButton, QVBoxLayout, QWidget)

from . import devices as dev, languages, theme
from . import library as lib
from .util import Failure, Poster, in_background, load_api_key, load_secret, reveal, save_api_key, save_secret, settings

LANGS = languages.CHOICES
SMTP_ACCOUNT = 'smtp-password'


def send_email(path, to, sender, host, port, user, password):
    """Emails the book to a Kindle's @kindle.com address. Amazon builds the library cover from the EPUB's own cover."""
    if os.path.getsize(path) > 50_000_000:
        raise Failure("The EPUB is over 50 MB, Amazon's email limit. Use the USB cable instead.")
    if not (host and sender and password):
        raise Failure('Fill in the email server, your address and its password (for Gmail, an app password) under Send › Wi-Fi.')
    msg = EmailMessage()
    msg['Subject'] = os.path.splitext(os.path.basename(path))[0]
    msg['From'] = sender
    msg['To'] = to
    msg.set_content(' ')
    with open(path, 'rb') as f:
        msg.add_attachment(f.read(), maintype='application', subtype='epub+zip', filename=os.path.basename(path))
    from .translate import ssl_context
    ctx = ssl_context()
    try:
        if int(port) == 465:
            with smtplib.SMTP_SSL(host, 465, context=ctx, timeout=120) as s:
                s.login(user or sender, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, int(port), timeout=120) as s:
                s.starttls(context=ctx)
                s.login(user or sender, password)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise Failure('The email server refused the password. For Gmail and Outlook, create an app password and use that.')
    except (smtplib.SMTPException, OSError) as e:
        raise Failure("Couldn't send the email: %s" % e)


class ForgeView(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.L = lib.shared()
        self.source = None
        self.book = None            # the source or result, once it's in the library
        self.epub = None
        self.is_pdf = False
        self.busy = False
        self.chapters = []
        self.style_sheet = ''

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(theme.Header('Convert & Translate'))
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(14)

        # drop zone
        self.drop = QFrame()
        self.drop.setObjectName('drop')
        self.drop.setStyleSheet('#drop { border: 2px dashed %s; border-radius: 16px; }' % theme.C['tertiary'])
        self.setAcceptDrops(True)
        dh = QHBoxLayout(self.drop)
        dh.setContentsMargins(20, 20, 20, 20)
        dh.setSpacing(20)
        self.cover_l = QLabel('📄', alignment=Qt.AlignmentFlag.AlignCenter)
        self.cover_l.setFixedSize(80, 110)
        dh.addWidget(self.cover_l)
        dv = QVBoxLayout()
        self.file_l = QLabel('Drop a PDF or EPUB')
        f = self.file_l.font()
        f.setBold(True)
        self.file_l.setFont(f)
        dv.addWidget(self.file_l)
        dv.addStretch(1)
        hint = QLabel('Scanned PDFs are read with Tesseract text recognition.')
        hint.setProperty('secondary', True)
        dv.addWidget(hint)
        self.choose_b = QPushButton('Choose File…')
        self.choose_b.clicked.connect(self.choose)
        dv.addWidget(self.choose_b, 0, Qt.AlignmentFlag.AlignLeft)
        dv.addStretch(1)
        dh.addLayout(dv, 1)
        v.addWidget(self.drop)

        # details
        self.details = QWidget()
        dl = QVBoxLayout(self.details)
        dl.setContentsMargins(0, 0, 0, 0)
        book_g = QGroupBox('Book')
        bf = QFormLayout(book_g)
        self.title_e = QLineEdit()
        self.author_e = QLineEdit()
        self.lang_c = QComboBox()
        for code, name in LANGS:
            self.lang_c.addItem(name, code)
        self.lang_c.currentIndexChanged.connect(self.language_changed)
        self.ocr_c = QCheckBox("Always use text recognition (ignore the PDF's own text)")
        bf.addRow('Title', self.title_e)
        bf.addRow('Author', self.author_e)
        bf.addRow('Written in', self.lang_c)
        bf.addRow(self.ocr_c)
        dl.addWidget(book_g)

        tr_g = QGroupBox('Translate')
        tf = QFormLayout(tr_g)
        self.to_c = QComboBox()
        self.to_c.currentIndexChanged.connect(self.update_ui)
        tf.addRow('Translate to', self.to_c)
        self.key_e = QLineEdit(load_api_key())
        self.key_e.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_e.setPlaceholderText('sk-ant-…  (kept in your system keyring)')
        self.key_e.editingFinished.connect(lambda: save_api_key(self.key_e.text()))
        self.key_row = QLabel('Claude API key')
        tf.addRow(self.key_row, self.key_e)
        self.tr_note = QLabel("Claude first studies the book's narration, each character's way of speaking and its names, then "
                              'translates with that style sheet. It uses your Anthropic API account (about $2–5 for a novel).')
        self.tr_note.setWordWrap(True)
        self.tr_note.setProperty('secondary', True)
        tf.addRow(self.tr_note)
        dl.addWidget(tr_g)

        send_g = QGroupBox('Send')
        sf = QFormLayout(send_g)
        self.usb_r = QRadioButton('USB cable')
        self.wifi_r = QRadioButton('Wi-Fi (Kindle email)')
        grp = QButtonGroup(self)
        grp.addButton(self.usb_r)
        grp.addButton(self.wifi_r)
        (self.wifi_r if settings.get('sendMethod') == 'wifi' else self.usb_r).setChecked(True)
        self.usb_r.toggled.connect(self.method_changed)
        seg = QFrame()
        seg.setObjectName('segments')
        sh = QHBoxLayout(seg)
        sh.setContentsMargins(2, 2, 2, 2)
        sh.setSpacing(0)
        for r in (self.usb_r, self.wifi_r):
            r.setProperty('segment', True)
            r.setCursor(Qt.CursorShape.PointingHandCursor)
            sh.addWidget(r)
        rr = QHBoxLayout()
        rr.addWidget(seg)
        rr.addStretch(1)
        sf.addRow('Send by', rr)
        self.wifi = QWidget()
        wf = QFormLayout(self.wifi)
        wf.setContentsMargins(0, 0, 0, 0)
        self.kindle_e = QLineEdit(settings.get('kindleEmail', ''), placeholderText='name@kindle.com')
        self.sender_e = QLineEdit(settings.get('senderEmail', ''), placeholderText='you@gmail.com')
        self.host_e = QLineEdit(settings.get('smtpHost', ''), placeholderText='smtp.gmail.com')
        self.port_s = QSpinBox()
        self.port_s.setRange(1, 65535)
        self.port_s.setValue(settings.get('smtpPort', 587))
        self.user_e = QLineEdit(settings.get('smtpUser', ''), placeholderText='usually your address')
        self.pass_e = QLineEdit(load_secret(SMTP_ACCOUNT))
        self.pass_e.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_e.setPlaceholderText('app password (kept in your system keyring)')
        for label, w, key in (('Kindle email', self.kindle_e, 'kindleEmail'), ('Send from', self.sender_e, 'senderEmail'),
                              ('Email server', self.host_e, 'smtpHost'), ('User name', self.user_e, 'smtpUser')):
            w.editingFinished.connect(lambda w=w, key=key: settings.set(key, w.text().strip()))
        self.port_s.valueChanged.connect(lambda n: settings.set('smtpPort', n))
        self.pass_e.editingFinished.connect(lambda: save_secret(self.pass_e.text(), SMTP_ACCOUNT))
        wf.addRow('Kindle email', self.kindle_e)
        wf.addRow('Send from', self.sender_e)
        srv = QHBoxLayout()
        srv.addWidget(self.host_e, 1)
        srv.addWidget(QLabel('Port'))
        srv.addWidget(self.port_s)
        wf.addRow('Email server', srv)
        wf.addRow('User name', self.user_e)
        wf.addRow('Password', self.pass_e)
        n = QLabel("The sender must be on Amazon's Approved Personal Document Email List.")
        n.setWordWrap(True)
        n.setProperty('secondary', True)
        wf.addRow(n)
        sf.addRow(self.wifi)
        self.usb_note = QLabel('Works with Kindle, Kobo, PocketBook and other e-readers over USB. On a Kindle, '
                               'EPUBs become Kindle books with covers in the Kindle library.')
        self.usb_note.setWordWrap(True)
        self.usb_note.setProperty('secondary', True)
        sf.addRow(self.usb_note)
        self.after_c = QCheckBox('Send when done')
        self.after_c.setChecked(True)
        sf.addRow(self.after_c)
        dl.addWidget(send_g)

        btns = QHBoxLayout()
        self.make_b = QPushButton('Make EPUB')
        self.make_b.setDefault(True)
        self.make_b.clicked.connect(self.make_epub)
        self.translate_b = QPushButton('Translate EPUB')
        self.translate_b.clicked.connect(lambda: self.translate_epub())
        self.send_b = QPushButton('Send to E-reader')
        self.send_b.clicked.connect(self.send)
        self.read_b = QPushButton('Read')
        self.read_b.clicked.connect(lambda: self.main.library.open(self.book))
        self.show_b = QPushButton('Show in Folder')
        self.show_b.clicked.connect(lambda: reveal(self.epub))
        for b in (self.make_b, self.translate_b, self.send_b):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.read_b)
        btns.addWidget(self.show_b)
        dl.addLayout(btns)
        v.addWidget(self.details)

        # status
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.status_l = QLabel('ℹ️  Drop a PDF to turn it into an EPUB, or an EPUB to translate or send.')
        self.status_l.setWordWrap(True)
        self.status_l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.bar)
        v.addWidget(self.status_l)
        self.style_b = QToolButton(text='▸ Style Claude learned from this book', checkable=True)
        self.style_b.setProperty('link', True)
        self.style_t = QTextEdit(readOnly=True)
        self.style_t.setMaximumHeight(160)
        self.style_b.toggled.connect(lambda on: (self.style_t.setVisible(on), self.style_b.setText(
            ('▾' if on else '▸') + ' Style Claude learned from this book')))
        self.ch_b = QToolButton(checkable=True)
        self.ch_b.setProperty('link', True)
        self.ch_t = QTextEdit(readOnly=True)
        self.ch_t.setMaximumHeight(120)
        self.ch_b.toggled.connect(lambda on: (self.ch_t.setVisible(on), self.update_ui()))
        for w in (self.style_b, self.style_t, self.ch_b, self.ch_t):
            v.addWidget(w, 0, Qt.AlignmentFlag.AlignLeft if isinstance(w, QToolButton) else Qt.AlignmentFlag(0))
        v.addStretch(1)
        self.update_ui()

    # MARK: state

    def lang(self):
        return self.lang_c.currentData()

    def target(self):
        return self.to_c.currentData() or ''

    def translating(self):
        return bool(self.target()) and self.target() != self.lang()

    def language_changed(self):
        keep = self.target()
        self.to_c.blockSignals(True)
        self.to_c.clear()
        self.to_c.addItem("Don't translate", '')
        for code, name in LANGS:
            if code != self.lang():
                self.to_c.addItem(name, code)
        i = self.to_c.findData(keep)
        self.to_c.setCurrentIndex(max(0, i))
        self.to_c.blockSignals(False)
        self.update_ui()

    def method_changed(self):
        settings.set('sendMethod', 'usb' if self.usb_r.isChecked() else 'wifi')
        self.update_ui()

    def update_ui(self):
        has = self.source is not None
        self.details.setVisible(has)
        self.ocr_c.setVisible(self.is_pdf)
        tr = self.translating()
        self.key_row.setVisible(tr)
        self.key_e.setVisible(tr)
        self.tr_note.setVisible(tr)
        wifi = self.wifi_r.isChecked()
        self.wifi.setVisible(wifi)
        self.usb_note.setVisible(not wifi)
        self.make_b.setVisible(self.is_pdf)
        self.make_b.setText('Make EPUB and Translate' if tr and self.epub is None else 'Make EPUB')
        self.make_b.setEnabled(not self.busy and bool(self.title_e.text().strip()))
        self.translate_b.setVisible(tr and self.epub is not None)
        self.translate_b.setEnabled(not self.busy)
        self.send_b.setText('Email to Kindle' if wifi else 'Send to E-reader')
        self.send_b.setEnabled(not self.busy and self.epub is not None)
        self.read_b.setVisible(self.book is not None)
        self.show_b.setVisible(bool(self.epub) and os.path.exists(self.epub or ''))
        for w in (self.choose_b, self.title_e, self.author_e, self.lang_c, self.ocr_c, self.to_c, self.key_e):
            w.setEnabled(not self.busy)
        self.bar.setVisible(self.busy)
        self.style_b.setVisible(bool(self.style_sheet))
        self.style_t.setVisible(bool(self.style_sheet) and self.style_b.isChecked())
        self.ch_b.setVisible(bool(self.chapters))
        self.ch_b.setText(('▾' if self.ch_b.isChecked() else '▸') + ' Chapters found (%d)' % len(self.chapters))
        self.ch_t.setVisible(bool(self.chapters) and self.ch_b.isChecked())

    def set_status(self, text, kind='info', progress=None):
        icon = {'info': 'ℹ️', 'work': '⏳', 'done': '✅', 'failed': '⚠️'}[kind]
        self.status_l.setText('%s  %s' % (icon, text))
        self.status_l.setStyleSheet('color: #ff3b30;' if kind == 'failed' else '')
        if progress is None:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(progress * 1000))

    def finish(self, text, ok=True):
        self.busy = False
        self.set_status(text, 'done' if ok else 'failed')
        self.update_ui()

    # MARK: loading

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(('.pdf', '.epub')):
                self.load(p)
                break

    def choose(self):
        f, _ = QFileDialog.getOpenFileName(self, 'Choose a Book', '', 'Books (*.pdf *.epub)')
        if f:
            self.load(f)

    def load(self, path, book=None):
        if self.busy:
            return
        self.source, self.book = path, book
        self.style_sheet, self.chapters, self.epub = '', [], None
        self.is_pdf = path.lower().endswith('.pdf')
        self.file_l.setText(os.path.basename(path))
        g_title, g_author = lib.guess_from_filename(path)
        title, author, language, cover = lib.read_meta(path)
        pm = QPixmap()
        if cover:
            pm.loadFromData(cover)
            self.cover_l.setPixmap(pm.scaled(80, 110, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.cover_l.setText('📘')
        if self.is_pdf:
            from .pdfread import page_count
            self.title_e.setText(title)
            self.author_e.setText(author)
            self.set_status('%d pages. Check the title and author, then make the EPUB.' % page_count(path))
        else:
            self.epub = path
            self.title_e.setText(book['title'] if book else title)
            self.author_e.setText(book['author'] if book else (author or g_author))
            language = (book['language'] if book else language) or 'en'
            self.set_status('EPUB ready to translate or send to your e-reader.')
        if not self.is_pdf or language:
            i = self.lang_c.findData(languages.known(languages.normalize(language)))
            self.lang_c.setCurrentIndex(i)
        self.language_changed()
        if self.target() == self.lang():
            self.to_c.setCurrentIndex(0)
        self.update_ui()

    def output_path(self, title):
        return self.L.unique_path(lib.file_name(title, self.author_e.text().strip()), 'epub')

    # MARK: PDF -> EPUB

    def make_epub(self):
        if not self.is_pdf or self.busy or not self.source:
            return
        from . import epubwriter, layout, pdfread
        src = self.source
        title, author, language = self.title_e.text().strip(), self.author_e.text().strip(), self.lang()
        force = self.ocr_c.isChecked()
        out = self.output_path(title)
        self.busy = True
        self.update_ui()
        self.set_status('Reading pages…', 'work', 0)
        say = Poster(lambda t, p=None: self.set_status(t, 'work', p))

        def work():
            pages = pdfread.read(src, language, force, lambda d, n: say('Reading page %d of %d…' % (d, n), d / max(1, n)))
            say('Rebuilding chapters and paragraphs…')
            blocks, start, end = layout.build(pages, src, language)
            blocks = layout.TextFixer(blocks).finalize(blocks)
            epubwriter.write(blocks, end, src, title, author, language, out)
            return [b[1] for b in blocks if b[0] == 'heading']

        def done(chapters):
            self.chapters = chapters
            self.epub = out
            added = self.L.add([out], quiet=True)
            self.book = added[0] if added else None
            self.finish('Added “%s” to your library — %d chapters.' % (title, len(chapters)))
            if self.translating():
                self.translate_epub()
            elif self.after_c.isChecked():
                self.send()
        in_background(work, done, lambda e: self.finish(str(e), False))

    # MARK: translation

    def translate_epub(self, confirmed=False):
        if not self.epub or not self.translating() or self.busy:
            return
        from . import translate
        key = self.key_e.text().strip()
        save_api_key(key)
        if not key:
            self.finish('Paste your Claude API key (console.anthropic.com › API Keys).', False)
            return
        if not confirmed:
            # show the rough cost before spending anything
            cost = translate.estimate(translate.source_chars(self.epub), self.lang(), self.target())
            r = QMessageBox.question(self, 'Translate with Claude?',
                                     "This sends the book's text to Anthropic and costs roughly $%.2f on your API account." % cost,
                                     QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Ok:
                return
        src, frm, to = self.epub, self.lang(), self.target()
        out = self.output_path('%s (%s)' % (self.title_e.text().strip(), (languages.get(to) or {}).get('native', to)))
        self.busy = True
        self.style_sheet = ''
        self.update_ui()
        self.set_status('Starting…', 'work', 0)
        say = Poster(lambda t, p: self.set_status(t, 'work', p))

        def done(report):
            self.style_sheet = report['style_sheet']
            self.style_t.setPlainText(self.style_sheet)
            self.epub = out
            added = self.L.add([out], quiet=True)
            self.book = added[0] if added else None
            msg = 'Added the translation to your library. Claude cost about $%.2f.' % report['cost']
            if report['left']:
                msg += " %d of %d passages couldn't be translated and were left as they were." % (report['left'], report['paragraphs'])
            self.finish(msg)
            if self.after_c.isChecked():
                self.send()
        in_background(lambda: translate.translate_epub(src, frm, to, out, key, say), done, lambda e: self.finish(str(e), False))

    # MARK: sending

    def send(self):
        if not self.epub or self.busy:
            return
        if self.wifi_r.isChecked():
            self.email(self.epub)
            return
        D = dev.shared()
        item = dev.send_item(self.book) if self.book else dev.send_item(
            file=self.epub, title=self.title_e.text().strip(), author=self.author_e.text().strip(), language=self.lang())
        self.busy = True
        self.update_ui()
        self.set_status('Preparing…', 'work')
        say = Poster(lambda t: self.set_status(t, 'work'))

        def work():
            from .util import calibre_app_running, quit_calibre
            if calibre_app_running():
                say('Closing Calibre (it holds the e-reader connection)…')
                if not quit_calibre():
                    raise Failure("Calibre didn't close (a dialog may be open in it). Close it yourself, then press Send.")
            return dev.send_now([item], say)

        def done(msg):
            self.finish(msg)
            D.scan()
        in_background(work, done, lambda e: (self.finish(str(e), False), D.scan()))

    def email(self, path):
        to = self.kindle_e.text().strip()
        if '@' not in to:
            self.finish("Enter your Kindle's email address (Amazon › Manage Your Content and Devices › Devices).", False)
            return
        save_secret(self.pass_e.text(), SMTP_ACCOUNT)
        args = (path, to, self.sender_e.text().strip(), self.host_e.text().strip(), self.port_s.value(),
                self.user_e.text().strip(), self.pass_e.text())
        self.busy = True
        self.update_ui()
        self.set_status('Sending by email…', 'work')
        in_background(lambda: send_email(*args),
                      lambda _: self.finish('Emailed to %s. It arrives over Wi-Fi in a few minutes. Amazon may email you '
                                            'to confirm it first.' % to),
                      lambda e: self.finish(str(e), False))

    def email_book(self, book):
        """Email to Kindle from the library's menu, with the settings from this screen."""
        if '@' not in self.kindle_e.text():
            QMessageBox.information(self, 'Email to Kindle', 'Add your Kindle email and mail server first: '
                                    'Convert & Translate › Send › Wi-Fi.')
            return
        L = self.L
        L.message = 'Sending “%s” by email…' % book['title']
        to = self.kindle_e.text().strip()
        args = (L.url(book), to, self.sender_e.text().strip(), self.host_e.text().strip(), self.port_s.value(),
                self.user_e.text().strip(), self.pass_e.text())
        in_background(lambda: send_email(*args),
                      lambda _: setattr(L, 'message', 'Emailed “%s” to %s. It arrives over Wi-Fi in a few minutes.' % (book['title'], to)),
                      lambda e: setattr(L, 'message', str(e)))
