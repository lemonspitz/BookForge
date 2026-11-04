"""A fallback for MTP readers the built-in connection can't reach: Calibre's device drivers through device.py
(only used when Calibre is installed)."""
import json, os, subprocess, sys, tempfile

from ..util import Failure, calibre_exe, no_window


def script_path():
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for p in (os.path.join(base, 'device.py'), os.path.join(base, 'bookforge', 'device.py'),
              os.path.join(base, '..', '..', 'Resources', 'device.py')):
        if os.path.exists(p):
            return os.path.abspath(p)
    raise Failure('device.py is missing from Book Forge.')


def script(command, payload=None, progress=None):
    exe = calibre_exe('calibre-debug')
    if not exe:
        raise Failure('Calibre is not installed.')
    args = [exe, '-e', script_path(), command]
    tmp = None
    if payload is not None:
        fd, tmp = tempfile.mkstemp(prefix='bookforge-', suffix='.json')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
        args.append(tmp)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=dict(os.environ, PYTHONIOENCODING='utf-8'), **no_window())
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
        raise Failure('Calibre gave no answer:\n' + '\n'.join(tail))
    return result


class CalibreReader:
    """Book-level operations (not file-level), so ops.py hands over to it whole."""
    bridge = True

    def __init__(self, info):
        self.info = info
        self.name, self.kind = info.get('name') or 'E-reader', info.get('kind') or 'generic'
        self.model = self.name

    @property
    def space(self):
        return self.info.get('total') or 0, self.info.get('free') or 0

    def refresh(self):
        r = script('info')
        if r.get('status') != 'ok':
            raise Failure(r.get('error') or "Calibre couldn't read the e-reader.")
        self.info = r

    def files(self):
        return [(b['lpath'] or b['path'], b.get('size') or 0) for b in self.info.get('books') or []]

    def list_books(self):
        return self.info.get('books') or []

    def upload(self, items, log):
        entries = [dict(file=it['file'], title=it['title'], authors=it['authors'], uuid=it['uuid'],
                        language=it.get('language') or '', cover=it.get('cover')) for it in items]
        r = script('upload', entries, log)
        if r.get('status') != 'uploaded':
            raise Failure(r.get('error') or 'The upload failed.')
        return r.get('count') or len(entries)

    def delete(self, paths):
        r = script('delete', paths)
        if r.get('status') != 'deleted':
            raise Failure(r.get('error') or "Couldn't remove books.")
        return len(paths)

    def fetch(self, paths, dest, log):
        r = script('fetch', dict(dest=dest, paths=paths), log)
        if r.get('status') != 'fetched':
            raise Failure(r.get('error') or "Couldn't copy from the reader.")
        return r.get('files') or [], r.get('failed') or []

    def exists(self, lpath):
        return any((b['lpath'] or b['path']).lower().startswith(lpath.lower()) for b in self.list_books())

    def eject(self):
        script('eject')

    def close(self):
        pass


def open_reader():
    """A CalibreReader for the attached reader, or raises Failure / Busy."""
    from .detect import Busy
    r = script('info')
    st = r.get('status')
    if st == 'ok':
        return CalibreReader(r)
    if st == 'open_failed':
        raise Busy(r.get('name') or 'Your e-reader')
    if st == 'no_device':
        return None
    raise Failure(r.get('error') or "Calibre couldn't read the e-reader.")
