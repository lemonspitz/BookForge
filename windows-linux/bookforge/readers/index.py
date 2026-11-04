"""What Book Forge knows about the books on a reader, kept on the reader itself in bookforge.json in its books
folder ({lpath: {title, authors, uuid}}). Calibre's metadata.calibre is read too, for books sent by older versions."""
import json

OURS = 'bookforge.json'


def ours(storage):
    return ('documents/' if storage.kind.startswith('kindle') else 'Books/') + OURS
CALIBRE = ('metadata.calibre', '.metadata.calibre')


def load(storage):
    """{lowercased lpath: dict(title, authors, uuid)} from the reader's index files (ours wins). Calibre writes
    MTP paths in lowercase, so paths are compared without case."""
    known = {}
    for name in CALIBRE:
        data = storage.read_small(name)
        if data:
            try:
                for b in json.loads(data.decode('utf-8')):
                    lp = (b.get('lpath') or '').replace('\\', '/').lower()
                    if lp:
                        known[lp] = dict(title=b.get('title') or '', authors=b.get('authors') or [], uuid=b.get('uuid') or '')
            except Exception:
                pass
    data = storage.read_small(ours(storage))
    if data:
        try:
            for lp, b in json.loads(data.decode('utf-8')).items():
                known[lp.lower()] = b
        except Exception:
            pass
    return known


def save(storage, entries):
    """Writes our index ({lpath: entry}) back to the reader."""
    storage.write_small(ours(storage), json.dumps(entries, ensure_ascii=False, indent=0, sort_keys=True).encode('utf-8'))
