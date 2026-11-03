# Run with: calibre-debug -e device.py <command> [list.json]
#   info                -> the connected e-reader's name, kind, storage and the books on it
#   upload <list.json>  -> [{"file", "title", "authors", "uuid", "language", "cover"}]; sends books,
#                          replacing older copies, with Kindle cover thumbnails
#   delete <list.json>  -> ["device path", ...]; removes books
#   eject
# Prints "PROGRESS {json}" lines while working and one "RESULT {json}" line at the end.
import sys, os, json, traceback
from calibre.customize.ui import device_plugins
from calibre.devices.scanner import DeviceScanner
from calibre.utils.config import device_prefs


def emit(tag, /, **kw):
    print(tag + ' ' + json.dumps(kw), flush=True)


def connect():
    """Finds and opens a reader the same way Calibre's ebook-device does. Returns (device, problem)."""
    scanner = DeviceScanner()
    scanner.scan()
    found = []
    for d in device_plugins():
        try:
            d.startup()
        except Exception:
            continue
        if d.MANAGES_DEVICE_PRESENCE:
            cd = d.detect_managed_devices(scanner.devices)
            if cd is not None:
                found.append((cd, d))
                break
            continue
        ok, det = scanner.is_device_connected(d)
        if ok:
            d.reset(detected_device=det)
            found.append((det, d))
    if not found:
        return None, dict(status='no_device')
    errors = []
    for det, d in found:
        try:
            d.open(det, 'bookforge')
        except Exception as e:
            errors.append(str(e)[:300])
            continue
        d.specialize_global_preferences(device_prefs)
        quiet(d)
        return d, None
    det, d = found[0]
    return None, dict(status='open_failed', error=' | '.join(errors), name=guess_name(d, det), kind=kind_of(d, ''))


def quiet(d):
    """Drivers report progress to Calibre's GUI; there is none here."""
    try:
        d.set_progress_reporter(lambda *a, **k: None)
    except Exception:
        d.report_progress = lambda *a, **k: None


def guess_name(d, det=None):
    for v in (getattr(d, 'current_friendly_name', None), getattr(det, 'product', None) if det is not None else None):
        if v:
            return str(v)
    try:
        return d.get_gui_name()
    except Exception:
        return 'E-reader'


def kind_of(d, name):
    cls = type(d).__name__.upper()
    n = (name or '').lower()
    if 'KINDLE' in cls or getattr(d, 'is_kindle', False) or 'kindle' in n:
        if 'scribe' in n:
            return 'kindle-scribe'
        if 'oasis' in n:
            return 'kindle-oasis'
        return 'kindle'
    if 'KOBO' in cls or 'kobo' in n:
        return 'kobo'
    if 'POCKETBOOK' in cls or 'pocketbook' in n:
        return 'pocketbook'
    if 'boox' in n or 'onyx' in n:
        return 'boox'
    if 'TOLINO' in cls or 'tolino' in n:
        return 'tolino'
    if 'NOOK' in cls or 'nook' in n:
        return 'nook'
    return 'generic'


def booklists(d):
    lists = [d.books()]
    for card in ('carda', 'cardb'):
        try:
            lists.append(d.books(oncard=card))
        except Exception:
            lists.append(d.booklist_class(None, None, None) if hasattr(d, 'booklist_class') else [])
    return lists


def info(d):
    dinfo = d.get_device_information(end_session=False)
    name = guess_name(d) if type(d).__name__ == 'MTP_DEVICE' else (dinfo[0] or guess_name(d))
    total = d.total_space(end_session=False)
    free = d.free_space(end_session=False)
    books = []
    for bl in booklists(d):
        for b in bl:
            books.append(dict(title=b.title or '', authors=list(b.authors or []), path=b.path,
                              lpath=getattr(b, 'lpath', '') or '', uuid=getattr(b, 'uuid', '') or '',
                              size=getattr(b, 'size', 0) or 0))
    return dict(status='ok', name=name, kind=kind_of(d, name), driver=type(d).__name__,
                formats=list(d.FORMATS), total=sum(x for x in total if x and x > 0),
                free=sum(x for x in free if x and x > 0), books=books)


def upload(d, items):
    from calibre.ebooks.metadata.meta import get_metadata
    from calibre.utils.img import scale_image
    from calibre.ebooks.metadata import authors_to_sort_string
    lists = booklists(d)
    # replace older copies of the same books
    uuids = {it['uuid'] for it in items}
    titles = {(it['title'].lower(), os.path.splitext(it['file'])[1].lower()) for it in items}
    old = [b.path for bl in lists for b in bl
           if getattr(b, 'uuid', None) in uuids
           or ((b.title or '').lower(), os.path.splitext(b.path)[1].lower()) in titles]
    if old:
        emit('PROGRESS', message='Replacing %d older cop%s…' % (len(old), 'y' if len(old) == 1 else 'ies'))
        d.delete_books(old, end_session=False)
        d.remove_books_from_metadata(old, lists)

    files, names, mis = [], [], []
    for it in items:
        ext = os.path.splitext(it['file'])[1][1:].lower()
        with open(it['file'], 'rb') as f:
            mi = get_metadata(f, ext)
        mi.title = it['title'] or mi.title
        if it.get('authors'):
            mi.authors = it['authors']
            mi.author_sort = authors_to_sort_string(mi.authors)
        mi.uuid = it['uuid']
        if it.get('language'):
            mi.languages = [it['language']]
        cover = None
        if it.get('cover') and os.path.exists(it['cover']):
            with open(it['cover'], 'rb') as f:
                cover = f.read()
        elif mi.cover_data and mi.cover_data[1]:
            cover = mi.cover_data[1]
        if cover:
            mi.thumbnail = scale_image(cover, width=10000, height=getattr(d, 'THUMBNAIL_HEIGHT', 320) * 2)
        files.append(it['file'])
        safe = ''.join(c for c in mi.title if c not in '/\\:*?"<>|')[:80].strip() or 'Book'
        names.append('%s - %s.%s' % (safe, it['uuid'][:8], ext))
        mis.append(mi)

    emit('PROGRESS', message='Copying %d book%s…' % (len(files), '' if len(files) == 1 else 's'))
    locations = d.upload_books(files, names, end_session=False, metadata=mis)
    d.add_books_to_metadata(locations, mis, lists)
    d.sync_booklists(lists, end_session=False)
    return dict(status='uploaded', count=len(files), replaced=len(old))


def delete(d, paths):
    lists = booklists(d)
    d.delete_books(paths, end_session=False)
    d.remove_books_from_metadata(paths, lists)
    d.sync_booklists(lists, end_session=False)
    return dict(status='deleted', count=len(paths))


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else 'info'
    arg = None
    if len(sys.argv) > 2:
        with open(sys.argv[2]) as f:
            arg = json.load(f)
    d, problem = connect()
    if d is None:
        emit('RESULT', **problem)
        return
    try:
        if command == 'info':
            res = info(d)
        elif command == 'upload':
            res = upload(d, arg)
        elif command == 'delete':
            res = delete(d, arg)
        elif command == 'eject':
            d.eject()
            res = dict(status='ejected')
        else:
            res = dict(status='error', error='unknown command ' + command)
        emit('RESULT', **res)
    except Exception as e:
        traceback.print_exc()
        emit('RESULT', status='error', error=str(e)[:400])
    finally:
        if command != 'eject':
            try:
                d.shutdown()
            except Exception:
                pass


main()
