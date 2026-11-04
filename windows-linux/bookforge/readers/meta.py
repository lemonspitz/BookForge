"""What a book file on a reader is: its title and author from the file name or the file's own header,
and whether it's locked with DRM."""
import os, re, struct

BOOK_EXTS = {'epub', 'kepub', 'azw', 'azw3', 'azw4', 'kfx', 'mobi', 'prc', 'pdb', 'pdf', 'fb2', 'fbz', 'cbz', 'cbr',
             'djvu', 'rtf', 'docx', 'lit', 'lrf'}
NOT_BOOKS = ('system/', 'koreader/', 'kmc/', 'extensions/', 'documents/dictionaries/', '.kobo/', '.adobe-digital-editions/')
STORE_ID = re.compile(r'[_-](asin_)?(B0[0-9A-Z]{8})([_.-].*)?$', re.I)


def ext(lpath):
    return lpath.rsplit('.', 1)[-1].lower() if '.' in lpath.rsplit('/', 1)[-1] else ''


def is_book(lpath):
    """Readers hold notes, dictionaries and their own software too; keep only e-books outside system folders."""
    lp = lpath.replace('\\', '/').lower()
    if ext(lp) not in BOOK_EXTS:
        return False
    if lp.startswith('.') or '/.' in lp or '.sdr/' in lp:
        return False
    return not any(lp.startswith(p) for p in NOT_BOOKS)


def skip_folder(lpath):
    """Folders that never hold books: hidden ones, the reader's system folders, and books' .sdr side folders."""
    lp = lpath.lower()
    return lp.rsplit('/', 1)[-1].startswith('.') or lp.endswith('.sdr') or any((lp + '/').startswith(p) for p in NOT_BOOKS)


def from_filename(lpath):
    """(title, [authors]) guessed from a file name like "Title - Author.azw3" or "Title_B076V53ZX8.kfx"."""
    stem = os.path.splitext(lpath.rsplit('/', 1)[-1])[0]
    if stem.lower().endswith('.kepub'):
        stem = stem[:-6]
    stem = STORE_ID.sub('', stem)
    stem = re.sub(r'\s+-\s+[0-9a-f]{8}$', '', stem)         # Book Forge's "Title - 1a2b3c4d" names
    parts = stem.split(' - ')
    title, authors = stem.strip(), []
    if len(parts) == 2 and parts[1].strip():
        title, authors = parts[0].strip(), [a.strip() for a in re.split(r'\s*(?:&|;)\s*', parts[1]) if a.strip()]
    # Calibre's sortable names: "Hobbit, The" -> "The Hobbit"
    m = re.match(r'^(.*), (The|A|An)$', title, re.I)
    if m:
        title = '%s %s' % (m.group(2), m.group(1))
    return title, authors


class Mobi:
    """Fields from the start of a MOBI/AZW/AZW3 file (the first 64 KB is plenty)."""

    def __init__(self, head):
        self.title = self.asin = self.cdetype = None
        self.authors = []
        self.encrypted = False
        if len(head) < 78 or head[60:68] not in (b'BOOKMOBI', b'TEXtREAd'):
            raise ValueError('not a MOBI file')
        count = struct.unpack('>H', head[76:78])[0]
        if count < 1 or len(head) < 78 + 8 * count:
            raise ValueError('short header')
        r0 = struct.unpack('>I', head[78:82])[0]
        rec = head[r0:]
        if len(rec) < 16:
            raise ValueError('short record 0')
        self.encrypted = struct.unpack('>H', rec[12:14])[0] != 0
        if rec[16:20] != b'MOBI':
            return
        hlen = struct.unpack('>I', rec[20:24])[0]
        enc = 'utf-8' if struct.unpack('>I', rec[28:32])[0] == 65001 else 'cp1252'
        name_off, name_len = struct.unpack('>II', rec[84:92])
        if name_off + name_len <= len(rec):
            self.title = rec[name_off:name_off + name_len].decode(enc, 'replace')
        exth_flags = struct.unpack('>I', rec[128:132])[0] if len(rec) >= 132 else 0
        if exth_flags & 0x40 and rec[16 + hlen:20 + hlen] == b'EXTH':
            pos = 16 + hlen
            n = struct.unpack('>I', rec[pos + 8:pos + 12])[0]
            p = pos + 12
            for _ in range(n):
                if p + 8 > len(rec):
                    break
                typ, size = struct.unpack('>II', rec[p:p + 8])
                val = rec[p + 8:p + size]
                p += size
                if typ == 100:
                    self.authors.append(val.decode(enc, 'replace').strip())
                elif typ == 503:
                    self.title = val.decode(enc, 'replace').strip()
                elif typ == 113:
                    self.asin = val.decode('ascii', 'replace').strip()
                elif typ == 501:
                    self.cdetype = val.decode('ascii', 'replace').strip()


def mobi(head):
    try:
        return Mobi(head)
    except Exception:
        return None


def from_file(path):
    """(title, [authors]) from a book file on the computer: EPUB package, MOBI/AZW header or PDF info."""
    e = ext(path)
    try:
        if e in ('azw', 'azw3', 'mobi', 'prc', 'pdb'):
            with open(path, 'rb') as f:
                m = mobi(f.read(65536))
            return (m.title, m.authors) if m and m.title else None
        if e in ('epub', 'kepub', 'pdf'):
            from ..library import read_meta
            title, author, _, _ = read_meta(path)
            if e == 'pdf' and not author and title == os.path.splitext(os.path.basename(path))[0]:
                return None
            return title, [a for a in author.split(' & ') if a]
    except Exception:
        return None
    return None
