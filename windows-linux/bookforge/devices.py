"""The USB e-reader, reached directly (readers/): drives, MTP readers, Windows Portable Devices.
Books are converted to and from Kindle formats by Book Forge itself (kindlebook, kindleread)."""
import json, os, re, subprocess, tempfile, threading, time, uuid

from .qt import QObject, QTimer, pyqtSignal, pyqtSlot

from . import library as lib
from .readers import detect, ops
from .readers.meta import ext as file_ext
from .util import (Failure, cache_dir, calibre_app_running, human_readable, no_window, quit_calibre,
                   settings)
from . import kindlebook, kindleread


# MARK: - Books on the reader (dicts: title, authors, path, lpath, uuid, size)

def file_name(b):
    return (b.get('lpath') or b.get('path') or '').replace('\\', '/').rsplit('/', 1)[-1]


def fmt(b):
    n = file_name(b)
    return n.rsplit('.', 1)[-1].lower() if '.' in n else ''


def display_title(b):
    return b.get('title') or os.path.splitext(file_name(b))[0]


def importable(b):
    """Amazon's KFX store books can't be converted or read outside a Kindle."""
    return fmt(b) != 'kfx'


STORE_NAME = re.compile(r'[_-](asin_)?B0[0-9A-Z]{8}([_.-]|$)', re.I)


def store_book(b):
    """Bought from the Kindle Store, so almost certainly locked with DRM: KFX and Print Replica files,
    and files the Kindle names with the book's Amazon ID ("…_B076V53ZX8.azw")."""
    return fmt(b) in ('kfx', 'azw4') or bool(STORE_NAME.search(file_name(b)))


KINDLE_FORMATS = ('azw', 'azw3', 'mobi', 'prc', 'pdb')


def needs_conversion(b):
    """Reader books that come into the library converted to EPUB (Kindle formats)."""
    return fmt(b) in KINDLE_FORMATS


def can_import(b):
    """Formats Book Forge can bring into the library: EPUB and PDF as they are, Kindle books converted."""
    return fmt(b) in ('epub', 'kepub', 'pdf') + KINDLE_FORMATS


def is_kindle(reader):
    return reader['kind'].startswith('kindle')


def short_name(reader):
    return 'Kindle' if is_kindle(reader) else reader['name']


def accepts(reader, format):
    """Whether a library book in this format can go on the reader. Kindles get EPUBs as Kindle books (AZW3)."""
    if is_kindle(reader):
        return format in ('pdf', 'epub')
    return format in reader['formats'] or (format == 'epub' and 'azw3' in reader['formats'])


FAMILY = {'kindle': 'Amazon Kindle', 'kindle-oasis': 'Amazon Kindle', 'kindle-scribe': 'Amazon Kindle',
          'kobo': 'Kobo', 'pocketbook': 'PocketBook', 'boox': 'Onyx Boox', 'tolino': 'Tolino', 'nook': 'Barnes & Noble Nook'}

def one_per_title(items, key, rank):
    """Keeps the best-ranked item of each title, in the original order."""
    best = {}
    for i, it in enumerate(items):
        k, r = key(it), rank(it)
        if k in best and best[k][0] <= r:
            continue
        best[k] = (r, i)
    keep = {i for _, i in best.values()}
    return [it for i, it in enumerate(items) if i in keep]


# MARK: - Converting

def readable(path):
    """A file fetched from a reader, as something the library can hold: EPUBs and PDFs as they are, Kindle
    books (AZW3, MOBI, AZW) converted to EPUB. Raises Failure, naming DRM if that's the problem."""
    e = path.lower().rsplit('.', 1)[-1]
    if e in ('epub', 'pdf'):
        return path
    if e not in KINDLE_FORMATS:
        raise Failure("Book Forge can't import .%s books." % e)
    out = os.path.splitext(path)[0] + '.converted.epub'
    try:
        kindleread.to_epub(path, out)
    except kindleread.DRMError:
        raise Failure('locked with DRM')
    except kindleread.ConversionError as ex:
        raise Failure(str(ex))
    return out


def prepare(item, reader, log):
    """The file to send to this reader: as it is, or as a Kindle book (AZW3). (None, reason) if it can't go."""
    ext = item['file'].rsplit('.', 1)[-1].lower()
    if is_kindle(reader):
        if ext == 'pdf':
            return item['file'], None
        if ext != 'epub':
            return None, 'format'
    elif ext in reader['formats']:
        return item['file'], None
    elif not (ext == 'epub' and 'azw3' in reader['formats']):
        return None, 'format'
    cache = os.path.join(cache_dir(), 'Converted')
    os.makedirs(cache, exist_ok=True)
    # the converter's version is in the name (the packaged app has no source files whose dates could say)
    out = os.path.join(cache, '%s-v%d.azw3' % (item['uuid'], kindlebook.CONVERTER_VERSION))
    newest = max(os.path.getmtime(item['file']),
                 os.path.getmtime(item['cover']) if item.get('cover') and os.path.exists(item['cover']) else 0)
    if os.path.exists(out) and os.path.getmtime(out) > newest:
        return out, None
    log('Converting “%s” for Kindle…' % item['title'])
    cover = None
    if item.get('cover') and os.path.exists(item['cover']):
        with open(item['cover'], 'rb') as f:
            cover = f.read()
    try:
        kindlebook.write(item['file'], out, asin=item['uuid'], cover=cover, title=item['title'], authors=item['authors'])
    except kindlebook.ConversionError as e:
        raise Failure('“%s” couldn\'t be made into a Kindle book: %s' % (item['title'], e))
    return out, None


def send_item(book=None, file=None, title='', author='', language=''):
    L = lib.shared()
    if book is not None:
        return dict(file=L.url(book), title=book['title'], authors=[a for a in book['author'].split(' & ') if a],
                    uuid=book['id'], language=book['language'], cover=L.cover_url(book))
    return dict(file=file, title=title, authors=[a for a in author.split(' & ') if a], uuid=str(uuid.uuid4()).upper(),
                language=language, cover=None)


def send_now(items, log):
    """Finds the reader and sends books to it in one step (Convert & Translate)."""
    log('Looking for your e-reader…')
    D = shared()
    D._rescan_blocking()
    if D.state == 'busy':
        raise Failure('%s is busy. Close Calibre or any other app using it, replug it and try again.' % D.state_name)
    if D.reader is None:
        raise Failure(D.state_name if D.state == 'failed' else
                      'No e-reader found. Connect it straight to the computer with a data cable (not a hub) and unlock it.')
    reader = D.reader
    with D._io:
        sent, skipped, need = D._upload(items, log)
    D._rescan_blocking()
    if sent == 0:
        raise Failure("%s can't open this format." % reader['name'])
    msg = 'On your %s%s' % (reader['name'], ', with its cover. You can unplug it.' if is_kindle(reader) else '. You can unplug it.')
    if skipped:
        msg += ' Skipped %s: format not supported.' % ', '.join(skipped)
    return msg


# MARK: - The connected reader

class Devices(QObject):
    changed = pyqtSignal()
    ask_quit_calibre = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.state = 'none'         # none, connected, busy, failed, ejected
        self.state_name = ''        # reader name for busy / ejected, error text for failed
        self.reader = None
        self.busy_kind = 'generic'
        self.scanning = False
        self.working = None         # what an action is doing right now
        self.message = None         # how the last action went
        self.last_seen = None
        self._running = False
        self._lock = threading.Lock()
        self._io = threading.RLock()     # one conversation with the reader at a time
        self.conn = self.conn_key = self.ejected_key = None
        self._pending = None
        self._by_id, self._by_title = {}, {}
        self.locked = set(settings.get('lockedReaderBooks', []))
        try:
            with open(self._seen_file(), encoding='utf-8') as f:
                self.last_seen = json.load(f)
        except Exception:
            self.last_seen = None
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.timeout.connect(self.scan)

    def _seen_file(self):
        return os.path.join(lib.shared().folder, '.last-reader.json')

    def _emit(self):
        self.changed.emit()

    # MARK: watching for readers

    usb_changed = pyqtSignal()

    def start(self):
        """Checks every few seconds what's attached (cheaply) and reads the reader whenever that changes."""
        self.usb_changed.connect(lambda: self.schedule_scan())
        threading.Thread(target=self._watch, daemon=True).start()
        self.schedule_scan(0)

    def _watch(self):
        last = None
        while True:
            try:
                with self._io:
                    sig = sorted(repr(k) for k, _ in detect.candidates())
            except Exception:
                sig = last
            if last is not None and sig != last:
                self.usb_changed.emit()     # queued to the GUI thread
            last = sig
            time.sleep(3)

    def schedule_scan(self, delay=2.0):
        """Readers take a moment to appear after they're plugged in, and one plug-in fires several events."""
        self._scan_timer.start(int(delay * 1000))

    def scan(self, then=None):
        if self._running:
            self.schedule_scan()
            return
        self._running = True
        self.scanning = True
        self._emit()

        def work():
            self._rescan_blocking(emit=False)
            self._running = False
            self.scanning = False
            self._emit()
            if then:
                then()
        threading.Thread(target=work, daemon=True).start()

    def _read(self):
        """(state, name or error, reader dict or None) for what's attached now. Keeps the open connection."""
        found = detect.candidates()
        keys = [k for k, _ in found]
        if self.conn is not None and self.conn_key not in keys:
            self.conn = self.conn_key = None        # unplugged: drop it without releasing
        if self.conn is None:
            if not found:
                return 'none', '', None
            # an MTP Kindle over a drive, a known reader over others
            found.sort(key=lambda kl: (kl[0][0] not in ('mtp', 'wpd', 'gvfs'), detect.kind_of(kl[1]) == 'generic'))
            key, label = found[0]
            if self.state == 'ejected' and self.ejected_key == key:
                return 'ejected', self.state_name, None
            try:
                self.conn = detect.open_reader(key, label)
                self.conn_key = key
            except detect.Busy as e:
                self.busy_kind = detect.kind_of(str(e))
                return 'busy', str(e) or 'Your e-reader', None
        st = self.conn
        files = st.files()
        books = ops.list_books(st)
        ops.save_title_cache()
        total, free = st.space
        koreader = st.kind.startswith('kindle') and ops.has_koreader(st, [lp for lp, _ in files])
        return 'connected', st.name, dict(name=st.name, kind=st.kind, driver=type(st).__name__,
                                          formats=ops.kind_formats(st.kind, koreader), total=total, free=free,
                                          books=books)

    def _rescan_blocking(self, emit=True):
        """Reads the reader again (any thread)."""
        try:
            with self._io:
                state, name, reader = self._read()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.conn = self.conn_key = None
            state, name, reader = 'failed', "Couldn't read the e-reader: %s" % e, None
        if state == 'ejected':
            self._set_reader(None)
        else:
            self.state, self.state_name = state, name
            self._set_reader(reader)
        if emit:
            self._emit()

    def _set_reader(self, reader):
        old = self.reader
        self.reader = reader
        books = reader['books'] if reader else []
        self._by_id = {b['uuid']: b for b in reversed(books) if b.get('uuid')}
        self._by_title = {}
        for b in books:
            self._by_title.setdefault(lib.key(display_title(b)), []).append(b)
        if reader and reader != old:
            self._remember(reader)

    def _remember(self, reader):
        seen = dict(name=reader['name'], kind=reader['kind'], date=lib.now_iso(),
                    ids=sorted({b['uuid'] for b in reader['books'] if b.get('uuid')}),
                    titles=sorted({lib.key(display_title(b)) for b in reader['books']}))
        self.last_seen = seen
        try:
            with open(self._seen_file(), 'w', encoding='utf-8') as f:
                json.dump(seen, f, ensure_ascii=False)
        except OSError:
            pass

    # MARK: what's where

    def copies(self, book):
        """Every copy of a library book on the reader: the one Book Forge put there (same id) and any with the same title."""
        out = [self._by_id[book['id']]] if book['id'] in self._by_id else []
        for d in self._by_title.get(lib.key(book['title']), []):
            if d not in out:
                out.append(d)
        return out

    def match(self, book):
        c = self.copies(book)
        return c[0] if c else None

    def was_on_reader(self, book):
        """Whether the book was on the reader when it was last connected (while it's unplugged)."""
        s = self.last_seen
        if self.reader is not None or not s:
            return False
        return book['id'] in s.get('ids', []) or lib.key(book['title']) in s.get('titles', [])

    def only_on_reader(self):
        """Books on the reader that aren't in the library."""
        books = lib.shared().books
        ids = {b['id'] for b in books}
        titles = {lib.key(b['title']) for b in books}
        return [d for d in (self.reader['books'] if self.reader else [])
                if d.get('uuid') not in ids and lib.key(display_title(d)) not in titles]

    def likely_locked(self, b):
        """Store books and books found to be locked: Import All leaves them on the reader."""
        return not importable(b) or store_book(b) or b['path'] in self.locked

    def to_import(self):
        """What one-click Import copies: books only on the reader that aren't locked, one copy per title
        (EPUB, then Kindle formats, then PDF)."""
        rank = {'epub': 0, 'kepub': 0, 'azw3': 1, 'mobi': 2, 'azw': 3, 'pdf': 9}
        return one_per_title([b for b in self.only_on_reader() if not self.likely_locked(b) and can_import(b)],
                             lambda b: lib.key(display_title(b)), lambda b: rank.get(fmt(b), 5))

    def to_send(self):
        """What one-click Send copies: library books not on the reader that it can open, one copy per title (EPUB before PDF)."""
        if not self.reader:
            return []
        L = lib.shared()
        return one_per_title([b for b in L.books if self.match(b) is None and accepts(self.reader, L.format(b))],
                             lambda b: lib.key(b['title']), lambda b: 0 if L.format(b) == 'epub' else 1)

    # MARK: changing what's on the reader

    def perform(self, action):
        """Runs a device action on a worker thread, first asking to close the Calibre app if it's open."""
        if calibre_app_running():
            self._pending = action
            self.ask_quit_calibre.emit()
        else:
            self._start(action)

    def _start(self, action):
        threading.Thread(target=self._guarded, args=(action,), daemon=True).start()

    def _guarded(self, action):
        try:
            action()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.message = str(e)
            self.working = None
            self._emit()

    def quit_calibre_and_continue(self):
        action, self._pending = self._pending, None

        def go():
            self.working = 'Closing Calibre…'
            self._emit()
            closed = quit_calibre()
            self.working = None
            if not closed:
                self.message = "Calibre didn't close (a dialog may be open in it). Close it yourself and try again."
                self._emit()
                return
            time.sleep(1)
            if action:
                action()
            else:
                self._rescan_blocking()
        self._start(go)

    def cancel_pending(self):
        self._pending = None

    def _say(self, line):
        self.working = line
        self._emit()

    def _begin(self):
        with self._lock:
            if self._running or self.reader is None:
                return False
            self._running = True
        self.message = None
        return True

    def _end(self):
        self.working = None
        self._running = False
        self._emit()

    def _upload(self, items, log):
        """Converts as needed and copies. Returns (sent, titles it can't open, titles that couldn't be converted).
        Hold _io."""
        reader = self.reader
        ready, skipped, failed = [], [], []
        for it in items:
            try:
                f, why = prepare(it, reader, log)
            except Failure as e:
                print(e)
                failed.append('“%s”' % it['title'])
                continue
            if f is None:
                skipped.append('“%s”' % it['title'])
                continue
            ready.append(dict(it, file=f))
        if not ready:
            return 0, skipped, failed
        sent = ops.upload(self.conn, ready, log)
        return sent, skipped, failed

    def sync(self, add, remove, note=''):
        """Removes and adds books in one go, then reads the reader again. Call from a worker thread."""
        if not self._begin():
            return
        reader = self.reader
        notes = []
        try:
            with self._io:
                if remove:
                    self._say('Removing %d book%s…' % (len(remove), '' if len(remove) == 1 else 's'))
                    ops.delete(self.conn, [b['path'] for b in remove])
                    notes.append('removed %d' % len(remove))
                need = []
                if add:
                    sent, skipped, need = self._upload([send_item(b) for b in add], self._say)
                    if sent:
                        notes.append('added %d' % sent)
                    if skipped:
                        notes.append('skipped %s (format not supported)' % ', '.join(skipped))
            msg = 'Done: ' + ', '.join(notes) + '.' if notes else 'Nothing to change.'
            msg += note
            if need:
                msg += " Couldn't convert %s for the %s (the EPUB may be damaged)." % (', '.join(need), short_name(reader))
            if is_kindle(reader) and any(lib.Library.format(b) == 'epub' for b in add):
                msg += ' Covers show once the Kindle is unplugged.'
            self.message = msg
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.message = str(e)
        self.working = None
        self._running = False
        self._rescan_blocking()

    def eject(self):
        if not self._begin():
            return
        name = self.reader['name']
        self._say('Ejecting…')
        try:
            with self._io:
                self.conn.eject()
        except Exception:
            pass
        self.busy_kind = self.reader['kind'] if self.reader else 'generic'
        self.ejected_key = self.conn_key
        self.conn = self.conn_key = None
        self._set_reader(None)
        self.state, self.state_name = 'ejected', name
        self._end()

    def import_books(self, books):
        """Copies books from the reader into the library. EPUBs and PDFs come as they are;
        Kindle books (AZW3, MOBI, AZW) become EPUBs; store books locked with DRM can't be copied.
        Returns the library books added. Call from a worker thread."""
        if not books or not self._begin():
            return []
        L = lib.shared()
        dest = tempfile.mkdtemp(prefix='bookforge-import-')
        try:
            def name(path):
                b = next((x for x in books if x['path'] == path), None)
                return '“%s”' % (display_title(b) if b else path.rsplit('/', 1)[-1])
            added, already, locked, failed, need = [], 0, [], [], []
            todo = []
            with self._io:
                for b in books:
                    if not can_import(b):
                        need.append(name(b['path']))
                    elif ops.locked(self.conn, b['path']):
                        # the file's own header says it's encrypted: don't copy it at all
                        locked.append(name(b['path']))
                        self.locked.add(b['path'])
                    else:
                        todo.append(b)
                settings.set('lockedReaderBooks', sorted(self.locked))
                self._say('Copying from the reader…')
                files, fails = ops.fetch(self.conn, [b['path'] for b in todo], dest, self._say)
            failed += [name(f['path']) for f in fails]
            by_path = {b['path']: b for b in todo}
            for f in files:
                path = f['file']
                d = by_path.get(f['path'])
                # a book with this title may have come into the library meanwhile (or earlier in this batch)
                if d and any(b['id'] == d.get('uuid') or lib.same(b['title'], display_title(d)) for b in L.books):
                    already += 1
                    continue
                if path.lower().rsplit('.', 1)[-1] not in ('epub', 'pdf'):
                    self._say('Converting %s to EPUB…' % name(f['path']))
                try:
                    ready = readable(path)
                except Failure as e:
                    if 'DRM' in str(e):
                        locked.append(name(f['path']))
                        self.locked.add(f['path'])
                        settings.set('lockedReaderBooks', sorted(self.locked))
                    else:
                        failed.append(name(f['path']))
                    continue
                new = L.add([ready], quiet=True)
                if not new:
                    already += 1
                    continue
                book = new[0]
                # use the reader's title so the two copies are recognised as the same book
                if d and d.get('title') and not lib.same(d['title'], book['title']):
                    book['title'] = d['title']
                    if d.get('authors'):
                        book['author'] = ' & '.join(d['authors'])
                    L.update(book)
                added.append(book)
            notes = []
            if added:
                notes.append('Imported “%s”.' % added[0]['title'] if len(added) == 1 else 'Imported %d books.' % len(added))
            if already:
                notes.append('%d %s already in the library.' % (already, 'was' if already == 1 else 'were'))
            if locked:
                one = len(locked) == 1
                notes.append("%s %s locked with DRM, so %s can't be copied." % (
                    ', '.join(locked), 'is a store book' if one else 'are store books', 'it' if one else 'they'))
            if need:
                notes.append("%s %s in a format Book Forge can't import." % (', '.join(need), 'is' if len(need) == 1 else 'are'))
            if failed:
                notes.append("Couldn't import %s." % ', '.join(failed))
            self.message = ' '.join(notes)
            return added
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.message = str(e)
            return []
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)
            self._end()

    # MARK: one click

    def import_new(self):
        """Copies every book that's only on the reader into the library. Books already in the library, second
        copies of the same title and store books are left alone. Call from a worker thread."""
        reader = self.reader
        if not reader:
            return
        books = self.to_import()
        store = sum(1 for b in self.only_on_reader() if self.likely_locked(b))
        need = sum(1 for b in self.only_on_reader() if not self.likely_locked(b) and not can_import(b))
        need_note = (" %d book%s in a format Book Forge can't import." % (need, ' is' if need == 1 else 's are')) if need else ''

        def store_note(other):
            if not store:
                return ''
            return ' Left %d%s store book%s on the %s (locked with DRM).' % (
                store, ' other' if other else '', '' if store == 1 else 's', short_name(reader))
        if not books:
            self.message = 'Every book on the %s is already in your library.%s%s' % (short_name(reader), store_note(False), need_note)
            self._emit()
            return
        before = len(self.locked)
        self.import_books(books)
        self.message = (self.message or '') + store_note(len(self.locked) > before) + need_note
        self._emit()

    def send_new(self):
        """Copies every library book that isn't on the reader yet. Nothing is removed, and books already on
        it aren't copied again. Call from a worker thread."""
        reader = self.reader
        if not reader:
            return
        L = lib.shared()
        books = self.to_send()
        already = sum(1 for b in L.books if self.match(b) is not None)
        waiting = [b for b in L.books if self.match(b) is None and L.format(b) == 'epub' and not accepts(reader, 'epub')]
        need_note = (" %d EPUB%s can't go on the %s, which doesn't open EPUBs." % (
            len(waiting), '' if len(waiting) == 1 else 's', short_name(reader))) if waiting else ''
        if not books:
            self.message = ('Every book in your library is already on the %s.' % short_name(reader)) if not waiting else need_note.strip()
            self._emit()
            return
        size = sum(os.path.getsize(L.url(b)) for b in books if os.path.exists(L.url(b)))
        if reader['free'] > 0 and size > reader['free'] - 20_000_000:
            self.message = ('Not enough space on the %s: these books need about %s and %s is free. '
                            'Tick fewer books below and press Sync.' % (short_name(reader), human_readable(size), human_readable(reader['free'])))
            self._emit()
            return
        note = ' %d %s already on it.' % (already, 'was' if already == 1 else 'were') if already else ''
        self.sync(books, [], note + need_note)


_shared = None


def shared():
    global _shared
    if _shared is None:
        _shared = Devices()
    return _shared
