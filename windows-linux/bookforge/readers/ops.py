"""Reading and changing what's on a reader, whatever the connection (a FolderStorage, an MTP storage)."""
import io, json, os, re

from . import index, meta

KINDLE_DIR, OTHER_DIR = 'documents/', 'Books/'


def kind_formats(kind, koreader=False):
    """What the reader opens, best first."""
    if kind.startswith('kindle'):
        return (['epub'] if koreader else []) + ['azw3', 'mobi', 'pdf', 'txt']
    if kind == 'kobo':
        return ['kepub', 'epub', 'pdf', 'cbz']
    if kind == 'pocketbook':
        return ['epub', 'pdf', 'fb2', 'mobi', 'djvu', 'cbz']
    return ['epub', 'pdf']


def book_dir(kind):
    return KINDLE_DIR if kind.startswith('kindle') else OTHER_DIR


def has_koreader(storage, files=None):
    if getattr(storage, 'bridge', False):
        return False
    names = files if files is not None else [lp for lp, _ in storage.files()]
    return storage.exists('koreader') or any(lp.lower().startswith('koreader/') for lp in names)


def list_books(storage):
    """[dict(title, authors, path, lpath, uuid, size)] for the books on the reader."""
    if getattr(storage, 'bridge', False):
        storage.refresh()
        return storage.list_books()
    known = index.load(storage)
    out = []
    for lpath, size in storage.files():
        if not meta.is_book(lpath):
            continue
        k = known.get(lpath.lower()) or {}
        title, authors = k.get('title'), k.get('authors')
        if not title:
            title, authors = read_title(storage, lpath, size)
        out.append(dict(title=title, authors=authors or [], path=lpath, lpath=lpath, uuid=k.get('uuid') or '', size=size))
    return out


_titles = None


def _title_cache():
    global _titles
    if _titles is None:
        from ..util import cache_dir
        try:
            with open(os.path.join(cache_dir(), 'reader-titles.json'), encoding='utf-8') as f:
                _titles = json.load(f)
        except Exception:
            _titles = {}
    return _titles


def save_title_cache():
    if _titles is not None:
        from ..util import cache_dir
        try:
            with open(os.path.join(cache_dir(), 'reader-titles.json'), 'w', encoding='utf-8') as f:
                json.dump(_titles, f, ensure_ascii=False)
        except OSError:
            pass


def read_title(storage, lpath, size):
    """(title, authors) for a book Book Forge didn't put there: from the file itself (cached by path and size),
    else from its name. Over MTP only the start of Kindle books is read, and EPUBs only when they're small."""
    fallback = meta.from_filename(lpath)
    e = meta.ext(lpath)
    if e not in ('azw', 'azw3', 'mobi', 'prc', 'pdb', 'epub', 'kepub', 'pdf'):
        return fallback
    cache = _title_cache()
    ck = '%s|%s|%d' % (storage.name, lpath, size)
    if ck in cache:
        return tuple(cache[ck]) if cache[ck] else fallback
    found = None
    local = getattr(storage, 'root', None)
    try:
        if local:
            found = meta.from_file(os.path.join(local, *lpath.split('/')))
        elif e in ('azw', 'azw3', 'mobi', 'prc', 'pdb'):
            m = meta.mobi(storage.head(lpath, 65536))
            found = (m.title, m.authors) if m and m.title else None
        elif e in ('epub', 'kepub') and size < 20_000_000:
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix='.epub')
            os.close(fd)
            try:
                storage.get(lpath, tmp)
                found = meta.from_file(tmp)
            finally:
                os.remove(tmp)
    except Exception:
        found = None
    cache[ck] = list(found) if found else None
    return found or fallback


def safe_name(title):
    return re.sub(r'[\\/:*?"<>|]', '', title)[:80].strip() or 'Book'


def kindle_thumbnail(storage, file, cover):
    """Kindles show sideloaded books' covers from system/thumbnails, named after the book's ASIN and type."""
    try:
        with open(file, 'rb') as f:
            m = meta.mobi(f.read(65536))
        if not m or not m.asin or not cover or not os.path.exists(cover):
            return
        from PIL import Image
        img = Image.open(cover).convert('RGB')
        img.thumbnail((330, 470))
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=90)
        storage.write_small('system/thumbnails/thumbnail_%s_%s_portrait.jpg' % (m.asin, m.cdetype or 'EBOK'), buf.getvalue())
    except Exception:
        pass


def upload(storage, items, log):
    """items: dict(file, title, authors, uuid, cover). Replaces older copies of the same books; returns how many were sent."""
    if getattr(storage, 'bridge', False):
        return storage.upload(items, log)
    books = list_books(storage)
    ours = index.load(storage)
    uuids = {it['uuid'] for it in items}
    titles = {(meta_key(it['title']), it['file'].rsplit('.', 1)[-1].lower()) for it in items}
    old = [b['lpath'] for b in books if b['uuid'] in uuids or (meta_key(b['title']), meta.ext(b['lpath'])) in titles]
    if old:
        log('Replacing %d older cop%s…' % (len(old), 'y' if len(old) == 1 else 'ies'))
        remove_files(storage, old)
        for lp in old:
            ours.pop(lp.lower(), None)
    folder = book_dir(storage.kind)
    present = {b['lpath'].lower() for b in books} - {lp.lower() for lp in old}
    for i, it in enumerate(items):
        e = it['file'].rsplit('.', 1)[-1].lower()
        lpath = '%s%s - %s.%s' % (folder, safe_name(it['title']), it['uuid'][:8].lower(), e)
        log('Copying %d of %d to %s…' % (i + 1, len(items), storage.name))
        storage.put(it['file'], lpath)
        if storage.kind.startswith('kindle') and e in ('azw3', 'mobi', 'azw'):
            kindle_thumbnail(storage, it['file'], it.get('cover'))
        ours[lpath.lower()] = dict(title=it['title'], authors=it['authors'], uuid=it['uuid'])
        present.add(lpath.lower())
    index.save(storage, {lp: v for lp, v in ours.items() if lp in present})
    return len(items)


def meta_key(title):
    from ..library import key
    return key(title)


def remove_files(storage, lpaths):
    for lp in lpaths:
        storage.remove(lp)
        if storage.kind.startswith('kindle'):
            # the Kindle keeps reading position and notes next to the book in "<name>.sdr"
            storage.remove(os.path.splitext(lp)[0] + '.sdr')


def delete(storage, lpaths):
    if getattr(storage, 'bridge', False):
        return storage.delete(lpaths)
    remove_files(storage, lpaths)
    ours = index.load(storage)
    for lp in lpaths:
        ours.pop(lp.lower(), None)
    present = {lp.lower() for lp, _ in storage.files()}
    index.save(storage, {lp: v for lp, v in ours.items() if lp in present})
    return len(lpaths)


def fetch(storage, lpaths, dest, log):
    """Copies books to the computer. Returns (files [dict(path, file)], failed [dict(path, error)])."""
    if getattr(storage, 'bridge', False):
        return storage.fetch(lpaths, dest, log)
    os.makedirs(dest, exist_ok=True)
    files, failed = [], []
    for i, lp in enumerate(lpaths):
        base = lp.rsplit('/', 1)[-1] or 'Book'
        stem, e = os.path.splitext(base)
        if stem.lower().endswith('.kepub'):     # Kobo's "book.kepub.epub"
            stem = stem[:-6]
        out = os.path.join(dest, stem + e)
        n = 2
        while os.path.exists(out):
            out = os.path.join(dest, '%s %d%s' % (stem, n, e))
            n += 1
        log('Copying %d of %d from the reader…' % (i + 1, len(lpaths)))
        try:
            storage.get(lp, out)
            files.append(dict(path=lp, file=out))
        except Exception as ex:
            if os.path.exists(out):
                os.remove(out)
            failed.append(dict(path=lp, error=str(ex)[:200]))
    return files, failed


def locked(storage, lpath):
    """True if the book is a MOBI/AZW file whose header says it's encrypted (DRM), None if unknown."""
    if meta.ext(lpath) not in ('azw', 'azw3', 'mobi', 'prc', 'pdb') or getattr(storage, 'bridge', False):
        return None
    try:
        m = meta.mobi(storage.head(lpath, 4096))
    except Exception:
        return None
    return m.encrypted if m else None
