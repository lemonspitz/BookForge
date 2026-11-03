"""The USB e-reader, reached through Calibre's device drivers (device.py, shared with the Mac app)."""
import json, os, re, subprocess, tempfile, threading, time, uuid

from qt.core import QObject, QTimer, pyqtSignal, pyqtSlot

from . import library as lib
from .util import Failure, cache_dir, calibre_app_running, calibre_exe, no_window, quit_calibre, run, settings


def script_path():
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, 'device.py'), os.path.join(here, '..', '..', 'Resources', 'device.py')):
        if os.path.exists(p):
            return os.path.abspath(p)
    raise Failure('device.py is missing from Book Forge.')


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


def is_kindle(reader):
    return reader['kind'].startswith('kindle')


def short_name(reader):
    return 'Kindle' if is_kindle(reader) else reader['name']


def accepts(reader, format):
    """Whether a library book in this format can go on the reader (Kindles get EPUBs converted)."""
    if is_kindle(reader):
        return format in ('epub', 'pdf')
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


# MARK: - Calibre plumbing (any thread)

def script(command, payload=None, progress=None):
    """Runs device.py through calibre-debug; returns its RESULT."""
    args = [calibre_exe('calibre-debug'), '-e', script_path(), command]
    tmp = None
    if payload is not None:
        fd, tmp = tempfile.mkstemp(prefix='bookforge-', suffix='.json')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
        args.append(tmp)
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, **no_window())
        result, tail = None, []
        for raw in p.stdout:
            line = raw.decode('utf-8', 'replace').rstrip('\r\n')
            if line.startswith('RESULT '):
                try:
                    result = json.loads(line[7:])
                except ValueError:
                    pass
            elif line.startswith('PROGRESS '):
                try:
                    msg = json.loads(line[9:]).get('message')
                except ValueError:
                    msg = None
                if msg and progress:
                    progress(msg)
            else:
                tail = (tail + [line])[-8:]
        p.wait()
    finally:
        if tmp:
            os.remove(tmp)
    if result is None:
        raise Failure("Calibre gave no answer:\n" + '\n'.join(tail))
    return result


def reader_from(r):
    return dict(name=r.get('name') or 'E-reader', kind=r.get('kind') or 'generic', driver=r.get('driver') or '',
                formats=r.get('formats') or [], total=r.get('total') or 0, free=r.get('free') or 0, books=r.get('books') or [])


def readable(path):
    """A file fetched from a reader, as something the library can hold: EPUBs and PDFs as they are,
    anything else (AZW3, MOBI, FB2…) converted to EPUB. Raises Calibre's error, which names DRM if that's the problem."""
    if path.lower().rsplit('.', 1)[-1] in ('epub', 'pdf'):
        return path
    out = os.path.splitext(path)[0] + '.converted.epub'
    run([calibre_exe('ebook-convert'), path, out])
    return out


def prepare(item, reader, log):
    """The file to send to this reader: as it is, or converted for Kindle. None if the reader can't open it."""
    ext = item['file'].rsplit('.', 1)[-1].lower()
    if is_kindle(reader):
        # Kindles don't open sideloaded EPUBs
        if ext == 'pdf':
            return item['file']
        wants_azw3 = ext == 'epub'
    elif ext in reader['formats']:
        return item['file']
    else:
        wants_azw3 = ext == 'epub' and 'azw3' in reader['formats']
    if not wants_azw3:
        return None
    cache = os.path.join(cache_dir(), 'Converted')
    os.makedirs(cache, exist_ok=True)
    out = os.path.join(cache, item['uuid'] + '.azw3')
    if os.path.exists(out) and os.path.getmtime(out) > os.path.getmtime(item['file']):
        return out
    log('Converting “%s” for Kindle…' % item['title'])
    cover = os.path.join(cache, item['uuid'] + '-cover.jpg')
    try:
        subprocess.run([calibre_exe('ebook-meta'), item['file'], '--get-cover=' + cover], capture_output=True, **no_window())
        args = [calibre_exe('ebook-convert'), item['file'], out, '--output-profile', 'kindle_pw3']
        if os.path.exists(cover):
            args += ['--cover', cover]
        run(args)
    finally:
        if os.path.exists(cover):
            os.remove(cover)
    return out


def upload(items, reader, log):
    """Converts as needed and uploads. Returns (sent, titles the reader can't open)."""
    entries, skipped = [], []
    for it in items:
        f = prepare(it, reader, log)
        if f is None:
            skipped.append('“%s”' % it['title'])
            continue
        entries.append(dict(file=f, title=it['title'], authors=[a for a in it['author'].split(' & ') if a],
                            uuid=it['uuid'], language=it['language']))
    if not entries:
        return 0, skipped
    log('Copying to %s…' % reader['name'])
    r = script('upload', entries, log)
    if r.get('status') != 'uploaded':
        raise Failure(r.get('error') or 'The upload failed (%s).' % r.get('status'))
    return r.get('count') or len(entries), skipped


def send_item(book=None, file=None, title='', author='', language=''):
    if book is not None:
        L = lib.shared()
        return dict(file=L.url(book), title=book['title'], author=book['author'], uuid=book['id'], language=book['language'])
    return dict(file=file, title=title, author=author, uuid=str(uuid.uuid4()).upper(), language=language)


def send_now(items, log):
    """Finds the reader and sends books to it in one step (Convert & Translate)."""
    log('Looking for your e-reader…')
    info = script('info')
    st = info.get('status')
    if st == 'no_device':
        raise Failure('No e-reader found. Connect it straight to the computer with a data cable (not a hub) and unlock it.')
    if st == 'open_failed':
        raise Failure('%s is busy. Close Calibre or any other app using it, replug it and try again.' % (info.get('name') or 'The e-reader'))
    if st != 'ok':
        raise Failure(info.get('error') or "Calibre couldn't read the e-reader.")
    reader = reader_from(info)
    sent, skipped = upload(items, reader, log)
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

    def start(self):
        """Watches the USB bus (cheaply, through Calibre's scanner) and reads the reader whenever it changes."""
        threading.Thread(target=self._watch, daemon=True).start()
        self.schedule_scan(0)

    def _watch(self):
        from calibre.devices.scanner import DeviceScanner
        scanner = DeviceScanner()
        last = None
        while True:
            try:
                scanner.scan()
                sig = sorted(repr(d) for d in scanner.devices)
            except Exception:
                sig = last
            if last is not None and sig != last:
                self._relay_schedule()
            last = sig
            time.sleep(3)

    def _relay_schedule(self):
        # QTimer must be started from the GUI thread
        from qt.core import QMetaObject, Qt
        QMetaObject.invokeMethod(self, '_schedule_default', Qt.ConnectionType.QueuedConnection)

    @pyqtSlot()
    def _schedule_default(self):
        self.schedule_scan()

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
        from .util import in_background

        def done(r):
            self._running = False
            self.scanning = False
            self._apply_info(r)
            self._emit()
            if then:
                then()

        def failed(e):
            self._running = False
            self.scanning = False
            self._set_reader(None)
            self.state, self.state_name = 'failed', str(e)
            self._emit()
        in_background(lambda: script('info'), done, failed)

    def _apply_info(self, r):
        st = r.get('status')
        if st == 'ok':
            found = reader_from(r)
            if self.state == 'ejected' and self.state_name == found['name']:
                return
            self._set_reader(found)
            self.state = 'connected'
        elif st == 'no_device':
            self._set_reader(None)
            self.state = 'none'
        elif st == 'open_failed':
            self._set_reader(None)
            self.busy_kind = r.get('kind') or 'generic'
            self.state, self.state_name = 'busy', r.get('name') or 'Your e-reader'
        else:
            self._set_reader(None)
            self.state, self.state_name = 'failed', r.get('error') or "Calibre couldn't read the e-reader."

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
        return one_per_title([b for b in self.only_on_reader() if not self.likely_locked(b)],
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

    def _rescan_blocking(self):
        """Reads the reader again from a worker thread."""
        try:
            r = script('info')
        except Exception as e:
            r = dict(status='error', error=str(e))
        self._apply_info(r)
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

    def sync(self, add, remove, note=''):
        """Removes and adds books in one go, then reads the reader again. Call from a worker thread."""
        if not self._begin():
            return
        reader = self.reader
        notes = []
        try:
            if remove:
                self._say('Removing %d book%s…' % (len(remove), '' if len(remove) == 1 else 's'))
                r = script('delete', [b['path'] for b in remove])
                if r.get('status') != 'deleted':
                    raise Failure(r.get('error') or "Couldn't remove books.")
                notes.append('removed %d' % len(remove))
            if add:
                sent, skipped = upload([send_item(b) for b in add], reader, self._say)
                if sent:
                    notes.append('added %d' % sent)
                if skipped:
                    notes.append('skipped %s (format not supported)' % ', '.join(skipped))
            msg = 'Done: ' + ', '.join(notes) + '.' if notes else 'Nothing to change.'
            msg += note
            if is_kindle(reader) and add:
                msg += ' Covers show once the Kindle is unplugged.'
            self.message = msg
        except Exception as e:
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
            script('eject')
        except Exception:
            pass
        self.busy_kind = self.reader['kind'] if self.reader else 'generic'
        self._set_reader(None)
        self.state, self.state_name = 'ejected', name
        self._end()

    def import_books(self, books):
        """Copies books from the reader into the library. Kindle books and other formats become EPUBs; store
        books locked with DRM can't be copied. Returns the library books added. Call from a worker thread."""
        if not books or not self._begin():
            return []
        L = lib.shared()
        dest = tempfile.mkdtemp(prefix='bookforge-import-')
        try:
            self._say('Copying from the reader…')
            r = script('fetch', dict(dest=dest, paths=[b['path'] for b in books]), self._say)
            if r.get('status') != 'fetched':
                raise Failure(r.get('error') or "Couldn't copy from the reader.")
            by_path = {b['path']: b for b in books}

            def name(path):
                b = by_path.get(path)
                return '“%s”' % (display_title(b) if b else path.replace('\\', '/').rsplit('/', 1)[-1])
            added, already, locked, failed = [], 0, [], [name(f['path']) for f in r.get('failed') or []]
            for f in r.get('files') or []:
                path = f.get('file')
                if not path:
                    continue
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
            if failed:
                notes.append("Couldn't import %s." % ', '.join(failed))
            self.message = ' '.join(notes)
            return added
        except Exception as e:
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

        def store_note(other):
            if not store:
                return ''
            return ' Left %d%s store book%s on the %s (locked with DRM).' % (
                store, ' other' if other else '', '' if store == 1 else 's', short_name(reader))
        if not books:
            self.message = 'Every book on the %s is already in your library.%s' % (short_name(reader), store_note(False))
            self._emit()
            return
        before = len(self.locked)
        self.import_books(books)
        self.message = (self.message or '') + store_note(len(self.locked) > before)
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
        if not books:
            self.message = 'Every book in your library is already on the %s.' % short_name(reader)
            self._emit()
            return
        size = sum(os.path.getsize(L.url(b)) for b in books if os.path.exists(L.url(b)))
        if reader['free'] > 0 and size > reader['free'] - 20_000_000:
            from calibre import human_readable
            self.message = ('Not enough space on the %s: these books need about %s and %s is free. '
                            'Tick fewer books below and press Sync.' % (short_name(reader), human_readable(size), human_readable(reader['free'])))
            self._emit()
            return
        note = ' %d %s already on it.' % (already, 'was' if already == 1 else 'were') if already else ''
        self.sync(books, [], note)


_shared = None


def shared():
    global _shared
    if _shared is None:
        _shared = Devices()
    return _shared
