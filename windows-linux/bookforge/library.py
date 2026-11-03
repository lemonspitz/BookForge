"""The catalogue: book files in one folder next to library.json and a .covers folder of thumbnails.
Same layout as the Mac app, so one library folder works with both."""
import datetime, io, os, posixpath, re, subprocess, tempfile, unicodedata, uuid, zipfile
from urllib.parse import unquote

from qt.core import QObject, QPixmap, pyqtSignal

from .util import Failure, documents_dir, no_window, pdf_tools, settings

FORMATS = ('epub', 'pdf')


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def key(s):
    """A title or name reduced to its letters and digits, so "The Hobbit: Or There and Back Again" and
    "the hobbit - or there and back again" are recognised as the same book."""
    folded = unicodedata.normalize('NFKD', s or '').casefold()
    folded = ''.join(c for c in folded if not unicodedata.combining(c))
    folded = unicodedata.normalize('NFKC', folded)
    k = ''.join(c for c in folded if c.isalnum())
    return k or folded.strip()


def same(a, b):
    return key(a) == key(b)


def guess_from_filename(path):
    """"Title -- Author -- ..." (Anna's Archive style) or "Title - Author"."""
    name = os.path.splitext(os.path.basename(path))[0]
    parts = name.split(' -- ')
    if len(parts) >= 2:
        return parts[0], parts[1]
    dash = name.split(' - ')
    if len(dash) == 2:
        return dash[0], dash[1]
    return name, ''


def normalize_language(code):
    c = re.split(r'[-_]', (code or '').lower())[0]
    return {'in': 'id', 'ind': 'id', 'eng': 'en', 'jp': 'ja', 'jpn': 'ja'}.get(c, c[:2])


def file_name(title, author):
    raw = '%s - %s' % (title, author) if author else title
    safe = raw.replace('/', '-').replace('\\', '-').replace(':', ' -')
    safe = re.sub(r'[*?"<>|]', '', safe).strip().rstrip('.')
    return (safe or 'Book')[:120]


def thumbnail(data, size=600):
    """A JPEG at most `size` pixels tall."""
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert('RGB')
        img.thumbnail((size, size))
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=85)
        return out.getvalue()
    except Exception:
        return None


# MARK: - Reading book files

def opf_path(zf):
    from lxml import etree
    try:
        c = etree.fromstring(zf.read('META-INF/container.xml'))
        el = c.xpath("//*[local-name()='rootfile']")
        if el:
            return el[0].get('full-path')
    except Exception:
        pass
    for n in zf.namelist():
        if n.lower().endswith('.opf'):
            return n
    return None


def resolve(href, base):
    """`href` as written in a file at `base` (both inside the archive) -> archive path."""
    clean = unquote(href.split('#')[0])
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), clean)).lstrip('./') if clean else base


def render_page(pdf, index, dpi, gray=False):
    """A page of a PDF as JPEG bytes (white background)."""
    _, pdftoppm, _ = pdf_tools()
    with tempfile.TemporaryDirectory() as d:
        args = [pdftoppm, '-r', str(dpi), '-f', str(index + 1), '-l', str(index + 1), '-jpeg', '-jpegopt', 'quality=85']
        if gray:
            args.append('-gray')
        subprocess.run(args + [pdf, os.path.join(d, 'p')], capture_output=True, **no_window())
        files = [f for f in os.listdir(d) if f.endswith('.jpg')]
        if not files:
            return None
        with open(os.path.join(d, files[0]), 'rb') as f:
            return f.read()


def pdf_info(pdf):
    pdfinfo, _, _ = pdf_tools()
    try:
        out = subprocess.run([pdfinfo, '-enc', 'UTF-8', pdf], capture_output=True, **no_window()).stdout.decode('utf-8', 'replace')
    except Exception:
        return {}
    info = {}
    for line in out.splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            info[k.strip()] = v.strip()
    return info


def read_meta(path):
    """(title, author, language, cover JPEG or None) from the book file."""
    g_title, g_author = guess_from_filename(path)
    if path.lower().endswith('.pdf'):
        info = pdf_info(path)
        page = render_page(path, 0, 72)
        return (info.get('Title') or g_title, info.get('Author') or g_author, '',
                thumbnail(page) if page else None)
    title, author, lang, cover = g_title, g_author, '', None
    from lxml import etree
    try:
        with zipfile.ZipFile(path) as zf:
            op = opf_path(zf)
            opf = etree.fromstring(zf.read(op))

            def texts(name):
                return [(n.text or '').strip() for n in opf.xpath("//*[local-name()='metadata']/*[local-name()='%s']" % name)
                        if (n.text or '').strip()]
            t = texts('title')
            if t:
                title = t[0]
            creators = [c for c in texts('creator') if not c.lower().startswith('unknown')]
            if creators:
                author = ' & '.join(creators)
            ls = texts('language')
            if ls:
                lang = normalize_language(ls[0])
            items = opf.xpath("//*[local-name()='manifest']/*[local-name()='item']")
            images = [i for i in items if (i.get('media-type') or '').startswith('image/')]
            cover_id = next(iter(opf.xpath("//*[local-name()='meta'][@name='cover']/@content")), None)
            item = next((i for i in images if 'cover-image' in (i.get('properties') or '').split()), None)
            if item is None:
                item = next((i for i in images if i.get('id') == cover_id), None)
            if item is None:
                item = next((i for i in images if 'cover' in ((i.get('id') or '') + (i.get('href') or '')).lower()), None)
            if item is not None and item.get('href'):
                try:
                    cover = thumbnail(zf.read(resolve(item.get('href'), op)))
                except KeyError:
                    pass
    except Exception:
        pass
    return title, author, lang, cover


def expand(paths):
    """Book files among `paths`, including every book inside folders."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            found = []
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                found += [os.path.join(root, f) for f in files
                          if not f.startswith('.') and f.lower().rsplit('.', 1)[-1] in FORMATS]
            out += sorted(found, key=str.lower)
        elif p.lower().rsplit('.', 1)[-1] in FORMATS:
            out.append(p)
    return out


# MARK: - The library

class Library(QObject):
    changed = pyqtSignal()
    message_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.folder = os.environ.get('BOOKFORGE_LIBRARY') or settings.get('library') \
            or os.path.join(documents_dir(), 'Book Forge Library')
        self.cover_folder = os.path.join(self.folder, '.covers')
        os.makedirs(self.cover_folder, exist_ok=True)
        self.catalog = os.path.join(self.folder, 'library.json')
        self.books = []
        self.importing = False
        self._message = None
        self._covers = {}
        try:
            import json
            with open(self.catalog, encoding='utf-8') as f:
                self.books = json.load(f)
        except Exception:
            self.books = []

    @property
    def message(self):
        return self._message

    @message.setter
    def message(self, m):
        self._message = m
        self.message_changed.emit()

    def url(self, book):
        return os.path.join(self.folder, book['file'])

    def cover_url(self, book):
        return os.path.join(self.cover_folder, book['id'] + '.jpg')

    def book(self, id):
        return next((b for b in self.books if b['id'] == id), None)

    @staticmethod
    def format(book):
        return book['file'].rsplit('.', 1)[-1].lower()

    def cover(self, book):
        if book['id'] not in self._covers:
            pm = QPixmap(self.cover_url(book))
            self._covers[book['id']] = None if pm.isNull() else pm
        return self._covers[book['id']]

    def rescan(self):
        """Picks up book files put into the folder by hand and forgets ones deleted there."""
        before = len(self.books)
        self.books = [b for b in self.books if os.path.exists(self.url(b))]
        known = {b['file'] for b in self.books}
        loose = [os.path.join(self.folder, f) for f in os.listdir(self.folder)
                 if f.lower().rsplit('.', 1)[-1] in FORMATS and f not in known and not f.startswith('.')]
        if loose:
            self.add(loose, quiet=True)
        elif len(self.books) != before:
            self.save()
            self.changed.emit()

    def add(self, paths, quiet=False, progress=None):
        """Copies books (or every book inside folders) into the library. Files already in the library
        folder are catalogued where they are. Returns the books added. Safe to call off the GUI thread
        only through `add_in_background`."""
        import shutil
        files = expand(paths)
        if not files:
            if not quiet:
                self.message = 'No EPUB or PDF files there.'
            return []
        added, skipped = [], 0
        for i, f in enumerate(files):
            if progress and len(files) > 1:
                progress('Adding %d of %d…' % (i + 1, len(files)))
            title, author, lang, cover = read_meta(f)
            ext = f.rsplit('.', 1)[-1].lower()
            in_folder = os.path.normcase(os.path.abspath(os.path.dirname(f))) == os.path.normcase(os.path.abspath(self.folder))
            if not in_folder and any(self.format(b) == ext and same(b['title'], title) and same(b['author'], author)
                                     for b in self.books):
                skipped += 1
                continue
            book = dict(id=str(uuid.uuid4()).upper(), file=os.path.basename(f), title=title, author=author,
                        language=lang, added=now_iso())
            if not in_folder:
                dest = self.unique_path(file_name(title, author), ext)
                try:
                    shutil.copyfile(f, dest)
                except OSError as e:
                    self.message = "Couldn't copy %s: %s" % (os.path.basename(f), e)
                    continue
                book['file'] = os.path.basename(dest)
            if cover:
                with open(self.cover_url(book), 'wb') as out:
                    out.write(cover)
            self.books.append(book)
            added.append(book)
        self.save()
        self.changed.emit()
        if not quiet:
            msg = 'Added “%s”.' % added[0]['title'] if len(added) == 1 else 'Added %d books.' % len(added)
            if skipped:
                msg += ' %d %s already in the library.' % (skipped, 'was' if skipped == 1 else 'were')
            self.message = msg
        return added

    def add_in_background(self, paths, done=None):
        from .util import Poster, in_background
        self.importing = True
        say = Poster(lambda m: setattr(self, 'message', m))

        def finished(r):
            self.importing = False
            self.changed.emit()
            if done:
                done(r)
        in_background(lambda: self.add(paths, progress=say), finished, lambda e: setattr(self, 'message', str(e)))

    def unique_path(self, base, ext):
        p = os.path.join(self.folder, '%s.%s' % (base, ext))
        n = 2
        while os.path.exists(p):
            p = os.path.join(self.folder, '%s %d.%s' % (base, n, ext))
            n += 1
        return p

    def remove(self, ids):
        """Moves the books' files to the Trash / Recycle Bin."""
        from calibre.utils.recycle_bin import delete_file
        for b in [b for b in self.books if b['id'] in ids]:
            try:
                delete_file(self.url(b))
            except Exception:
                pass
            try:
                os.remove(self.cover_url(b))
            except OSError:
                pass
            self._covers.pop(b['id'], None)
        self.books = [b for b in self.books if b['id'] not in ids]
        self.save()
        self.changed.emit()
        self.message = 'Moved the book to the Trash.' if len(ids) == 1 else 'Moved %d books to the Trash.' % len(ids)

    def update(self, book):
        for i, b in enumerate(self.books):
            if b['id'] == book['id']:
                self.books[i] = book
                self.save()
                self.changed.emit()
                return

    def set_position(self, id, chapter, fraction, progress):
        b = self.book(id)
        if not b:
            return
        pos = dict(chapter=chapter, fraction=fraction, progress=progress)
        if b.get('position') != pos:
            b['position'] = pos
            self.save()

    def save(self):
        import json
        tmp = self.catalog + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.books, f, indent=2, sort_keys=True, ensure_ascii=False)
        os.replace(tmp, self.catalog)


_shared = None


def shared():
    global _shared
    if _shared is None:
        _shared = Library()
    return _shared
