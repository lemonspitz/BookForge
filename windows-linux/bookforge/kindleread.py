"""Kindle books (AZW3, MOBI, AZW, PRC) → EPUB, without Calibre.

    to_epub(path, out_path)      raises DRMError for store books locked with DRM

Reads the Palm database, unpacks the text (PalmDOC or HUFF/CDIC compression) and rebuilds the book: KF8 books
(AZW3, and the KF8 half of newer MOBI files) from their skeleton and fragment indexes, older MOBI books from
their single HTML stream, split at page breaks. Images, fonts, style sheets, links, the table of contents, the
cover and the metadata come across.
"""
import html, posixpath, re, struct, uuid, zipfile, zlib

from lxml import etree, html as lhtml

NULL = 0xffffffff
XHTML = 'http://www.w3.org/1999/xhtml'
LANGS = {0x09: 'en', 0x21: 'id', 0x11: 'ja', 0x12: 'ko', 0x804: 'zh-Hans', 0x404: 'zh-Hant', 0xc04: 'zh-Hant',
         0x04: 'zh', 0x1e: 'th', 0x2a: 'vi', 0x0c: 'fr', 0x07: 'de', 0x0a: 'es', 0x10: 'it', 0x16: 'pt', 0x13: 'nl',
         0x19: 'ru', 0x15: 'pl', 0x1f: 'tr', 0x01: 'ar'}


class DRMError(Exception):
    pass


class ConversionError(Exception):
    pass


def u16(b, o):
    return struct.unpack_from('>H', b, o)[0]


def u32(b, o):
    return struct.unpack_from('>I', b, o)[0]


def decint(b, pos):
    """Forward variable-width integer (the last byte has its high bit set)."""
    v = 0
    while True:
        c = b[pos]
        pos += 1
        v = (v << 7) | (c & 0x7f)
        if c & 0x80:
            return v, pos


# MARK: - decompression

def palmdoc(data):
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        i += 1
        if c == 0 or 9 <= c <= 0x7f:
            out.append(c)
        elif c <= 8:
            out += data[i:i + c]
            i += c
        elif c >= 0xc0:
            out.append(0x20)
            out.append(c ^ 0x80)
        elif i < n:
            pair = (c << 8) | data[i]
            i += 1
            dist, length = (pair >> 3) & 0x7ff, (pair & 7) + 3
            if dist == 0 or dist > len(out):
                continue
            for _ in range(length):
                out.append(out[-dist])
    return bytes(out)


class Huff:
    """HUFF/CDIC compression (Amazon's dictionary coding): a code table, then phrase dictionaries that may
    themselves be compressed."""
    def __init__(self, huff, cdics):
        if huff[:4] != b'HUFF':
            raise ConversionError('Bad HUFF record.')
        t1, t2 = u32(huff, 8), u32(huff, 12)
        self.table = []
        for v in struct.unpack_from('>256I', huff, t1):
            bits, term, high = v & 0x1f, v & 0x80, v >> 8
            self.table.append((bits, term, ((high + 1) << (32 - bits)) - 1))
        pairs = struct.unpack_from('>64I', huff, t2)
        self.lowest = [0] + [pairs[2 * i] << (32 - (i + 1)) for i in range(32)]
        self.highest = [0] + [((pairs[2 * i + 1] + 1) << (32 - (i + 1))) - 1 for i in range(32)]
        self.phrases = []
        for c in cdics:
            if c[:4] != b'CDIC':
                raise ConversionError('Bad CDIC record.')
            count, bits = u32(c, 8), u32(c, 12)
            n = min(1 << bits, count - len(self.phrases))
            for off in struct.unpack_from('>%dH' % n, c, 16):
                ln = u16(c, 16 + off)
                self.phrases.append([c[18 + off:18 + off + (ln & 0x7fff)], bool(ln & 0x8000)])

    def unpack(self, data):
        left = len(data) * 8
        data = data + b'\0' * 8
        pos, x, n, out = 0, struct.unpack_from('>Q', data, 0)[0], 32, bytearray()
        while True:
            if n <= 0:
                pos += 4
                x = struct.unpack_from('>Q', data, pos)[0]
                n += 32
            code = (x >> n) & 0xffffffff
            bits, term, high = self.table[code >> 24]
            if not term:
                while code < self.lowest[bits]:
                    bits += 1
                high = self.highest[bits]
            n -= bits
            left -= bits
            if left < 0:
                break
            p = self.phrases[(high - code) >> (32 - bits)]
            if not p[1]:
                # a compressed phrase: expand it once (marked first, so a phrase can't recurse into itself)
                p[1] = True
                p[0] = self.unpack(p[0])
            out += p[0]
        return bytes(out)


# MARK: - the Palm database

class Section:
    """One book inside the file: the MOBI 6 part, or the KF8 part (whole file for AZW3)."""
    def __init__(self, records, base):
        self.records, self.base = records, base
        r0 = records[base]
        self.r0 = r0
        self.compression = u16(r0, 0)
        self.text_length = u32(r0, 4)
        self.text_count = u16(r0, 8)
        self.encryption = u16(r0, 12)
        self.mobi = r0[16:20] == b'MOBI'
        self.version = 0
        self.encoding = 'cp1252'
        self.extra_flags = 0
        self.exth = {}
        self.name = ''
        self.locale = 0
        self.first_resource = NULL
        self.huff = (NULL, 0)
        self.fdst = NULL
        self.ncx = self.frag = self.skel = NULL
        if not self.mobi:
            return
        hlen = u32(r0, 20)
        self.encoding = 'utf-8' if u32(r0, 28) == 65001 else 'cp1252'
        self.version = u32(r0, 36)
        name_off, name_len = u32(r0, 84), u32(r0, 88)
        self.name = r0[name_off:name_off + name_len].decode(self.encoding, 'replace')
        self.locale = u32(r0, 92)
        self.first_resource = u32(r0, 108)
        self.huff = (u32(r0, 112), u32(r0, 116))
        if hlen >= 0xe4:
            self.extra_flags = u16(r0, 0xf2)
        if self.version >= 8 and hlen >= 0xb8:
            self.fdst = u32(r0, 0xc0)
        if hlen >= 0xe8:
            self.ncx = u32(r0, 0xf4)
        if self.version >= 8 and hlen >= 0xf0:
            self.frag, self.skel = u32(r0, 0xf8), u32(r0, 0xfc)
        if u32(r0, 128) & 0x40 and r0[16 + hlen:20 + hlen] == b'EXTH':
            p = 16 + hlen
            count = u32(r0, p + 8)
            p += 12
            for _ in range(count):
                if p + 8 > len(r0):
                    break
                typ, size = u32(r0, p), u32(r0, p + 4)
                self.exth.setdefault(typ, []).append(r0[p + 8:p + size])
                p += size

    def rec(self, i):
        """A record by its number in this section's header (those count from the section's record 0)."""
        return self.records[self.base + i]

    def ex_str(self, code):
        return [v.decode(self.encoding, 'replace').strip() for v in self.exth.get(code, [])]

    def ex_int(self, code):
        v = self.exth.get(code)
        return u32(v[0], 0) if v and len(v[0]) >= 4 else None

    @staticmethod
    def trailing_size(data):
        size, shift = 0, 0
        for i in range(len(data) - 1, max(-1, len(data) - 5), -1):
            c = data[i]
            size |= (c & 0x7f) << shift
            shift += 7
            if c & 0x80:
                break
        return size

    def text(self):
        if self.encryption:
            raise DRMError('This book is locked with DRM.')
        if self.compression == 17480:
            start, count = self.huff
            h = Huff(self.rec(start), [self.rec(start + i) for i in range(1, count)])
            unpack = h.unpack
        elif self.compression == 2:
            unpack = palmdoc
        elif self.compression == 1:
            unpack = bytes
        else:
            raise ConversionError('Unknown compression (%d).' % self.compression)
        out = bytearray()
        for i in range(1, self.text_count + 1):
            data = self.rec(i)
            flags = self.extra_flags >> 1
            while flags:
                if flags & 1:
                    size = self.trailing_size(data)
                    data = data[:len(data) - size] if size <= len(data) else b''
                flags >>= 1
            if self.extra_flags & 1 and data:
                data = data[:len(data) - ((data[-1] & 3) + 1)]
            out += unpack(data)
        return bytes(out[:self.text_length] if self.text_length else out)


def read_index(sec, start):
    """[(key, {tag: [values]})] and a function giving CNCX strings, from the INDX records at `start`."""
    if start in (NULL, None) or sec.base + start >= len(sec.records):
        return [], lambda o: ''
    head = sec.rec(start)
    if head[:4] != b'INDX':
        return [], lambda o: ''
    count, ncncx = u32(head, 24), u32(head, 52)
    t = head.find(b'TAGX')
    tlen, cbcount = u32(head, t + 4), u32(head, t + 8)
    tags = [tuple(head[t + i:t + i + 4]) for i in range(12, tlen, 4)]
    cncx = [sec.rec(start + 1 + count + i) for i in range(ncncx)]
    entries = []
    for r in range(count):
        rec = sec.rec(start + 1 + r)
        idxt, n = u32(rec, 20), u32(rec, 24)
        offs = [u16(rec, idxt + 4 + 2 * i) for i in range(n)] + [idxt]
        for i in range(n):
            pos = offs[i]
            klen = rec[pos]
            key = rec[pos + 1:pos + 1 + klen].decode('latin-1')
            pos += 1 + klen
            cbs = rec[pos:pos + cbcount]
            pos += cbcount
            plan, cbi = [], 0
            for tag, vpe, mask, end in tags:
                if end:
                    cbi += 1
                    continue
                v = cbs[cbi] & mask if cbi < len(cbs) else 0
                if not v:
                    continue
                if v == mask and bin(mask).count('1') > 1:
                    nbytes, pos = decint(rec, pos)
                    plan.append((tag, None, nbytes, vpe))
                else:
                    m = mask
                    while not m & 1:
                        m >>= 1
                        v >>= 1
                    plan.append((tag, v, None, vpe))
            vals = {}
            for tag, cnt, nbytes, vpe in plan:
                got = []
                if cnt is not None:
                    for _ in range(cnt * vpe):
                        x, pos = decint(rec, pos)
                        got.append(x)
                else:
                    stop = pos + nbytes
                    while pos < stop:
                        x, pos = decint(rec, pos)
                        got.append(x)
                vals[tag] = got
            entries.append((key, vals))

    def string(off):
        try:
            r = cncx[off // 0x10000]
            ln, p = decint(r, off % 0x10000)
            return r[p:p + ln].decode('utf-8', 'replace')
        except Exception:
            return ''
    return entries, string


def toc_order(entries):
    """NCX index entries are stored level by level; put them in reading order, children under parents.
    entries: [(values, item)] in index order. Returns [(values, item, depth)]."""
    out, seen = [], set()

    def visit(i, depth):
        if i in seen or i >= len(entries):
            return
        seen.add(i)
        v, item = entries[i]
        out.append((v, item, depth))
        if 22 in v and 23 in v:
            for c in range(v[22][0], v[23][0] + 1):
                visit(c, depth + 1)
    for i, (v, _) in enumerate(entries):
        if 21 not in v:
            visit(i, 0)
    for i in range(len(entries)):
        visit(i, 0)
    return out


# MARK: - resources

def image_ext(data):
    if data[:3] == b'\xff\xd8\xff':
        return 'jpg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if data[:2] == b'BM':
        return 'bmp'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    if data[:5] == b'<?xml' or data[:4] == b'<svg':
        return 'svg'
    return None


def font_record(data):
    """A FONT record's font: optionally XOR-obfuscated, optionally zlib-compressed."""
    size, flags, start, xor_len, xor_start = struct.unpack_from('>5I', data, 4)
    font = bytearray(data[start:])
    if flags & 2 and xor_len:
        key = data[xor_start:xor_start + xor_len]
        for i in range(min(1040, len(font))):
            font[i] ^= key[i % xor_len]
    font = bytes(font)
    if flags & 1:
        font = zlib.decompress(font)
    ext = 'otf' if font[:4] == b'OTTO' else 'woff' if font[:4] == b'wOFF' else 'ttf'
    return font, ext


class Resources:
    """kindle:embed numbering: the resource records in order, counted from 1."""
    SKIP = {b'FLIS', b'FCIS', b'SRCS', b'\xe9\x8e\r\n', b'BOUN', b'FDST', b'DATP', b'AUDI', b'VIDE', b'RESC',
            b'CMET', b'PAGE', b'INDX', b'CONT', b'CRES', b'kind'}

    def __init__(self, records, ranges):
        self.items = []         # (zip path, bytes, media type) or None
        for start, end in ranges:
            for i in range(start, min(end, len(records))):
                data = records[i]
                item = None
                if data[:4] == b'FONT':
                    try:
                        font, ext = font_record(data)
                        item = ('fonts/font%04d.%s' % (len(self.items) + 1, ext), font,
                                {'otf': 'font/otf', 'woff': 'font/woff'}.get(ext, 'font/ttf'))
                    except Exception:
                        pass
                elif data[:4] not in self.SKIP:
                    ext = image_ext(data)
                    if ext:
                        mt = {'jpg': 'image/jpeg', 'svg': 'image/svg+xml'}.get(ext, 'image/' + ext)
                        item = ('images/image%04d.%s' % (len(self.items) + 1, ext), data, mt)
                self.items.append(item)

    def get(self, n):
        """1-based."""
        return self.items[n - 1] if 0 < n <= len(self.items) else None

    def used(self):
        return [x for x in self.items if x]


# MARK: - building the EPUB

class Out:
    def __init__(self, title, authors, language, publisher='', description='', date='', ident=None):
        self.title, self.authors, self.language = title, authors, language or 'en'
        self.publisher, self.description, self.date = publisher, description, date
        self.ident = ident or 'urn:uuid:%s' % uuid.uuid4()
        self.files = {}         # zip path -> bytes
        self.types = {}         # zip path -> media type
        self.spine = []
        self.toc = []           # (label, href, depth)
        self.cover = None

    def add(self, path, data, mt):
        self.files[path] = data
        self.types[path] = mt

    def write(self, out_path):
        e = html.escape
        items, refs = [], []
        ids = {}
        for n, path in enumerate(sorted(self.files)):
            ids[path] = 'i%d' % n
            props = ' properties="cover-image"' if path == self.cover else ''
            items.append('<item id="%s" href="%s" media-type="%s"%s/>' % (ids[path], e(path), self.types[path], props))
        for p in self.spine:
            refs.append('<itemref idref="%s"/>' % ids[p])
        meta = ['<dc:identifier id="uid">%s</dc:identifier>' % e(self.ident),
                '<dc:title>%s</dc:title>' % e(self.title), '<dc:language>%s</dc:language>' % e(self.language),
                '<meta property="dcterms:modified">2000-01-01T00:00:00Z</meta>']
        meta += ['<dc:creator>%s</dc:creator>' % e(a) for a in self.authors]
        if self.publisher:
            meta.append('<dc:publisher>%s</dc:publisher>' % e(self.publisher))
        if self.description:
            meta.append('<dc:description>%s</dc:description>' % e(self.description))
        if self.date:
            meta.append('<dc:date>%s</dc:date>' % e(self.date))
        if self.cover:
            meta.append('<meta name="cover" content="%s"/>' % ids[self.cover])
        toc = self.toc or [(self.title, self.spine[0], 0)]
        # nav document and NCX
        nav, ncx, depth, play = [], [], -1, 0
        for label, href, d in toc:
            d = max(0, min(d, depth + 1))
            while depth > d:
                nav.append('</li></ol>')
                depth -= 1
            if depth == d:
                nav.append('</li>')
            else:
                nav.append('<ol>')
                depth = d
            nav.append('<li><a href="%s">%s</a>' % (e(href), e(label)))
        while depth >= 0:
            nav.append('</li></ol>')
            depth -= 1
        stack = []
        for label, href, d in toc:
            while stack and stack[-1] >= d:
                ncx.append('</navPoint>')
                stack.pop()
            play += 1
            ncx.append('<navPoint id="n%d" playOrder="%d"><navLabel><text>%s</text></navLabel><content src="%s"/>'
                       % (play, play, e(label), e(href)))
            stack.append(d)
        ncx += ['</navPoint>'] * len(stack)
        nav_doc = ('<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" '
                   'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>%s</title></head><body>'
                   '<nav epub:type="toc" id="toc">%s</nav></body></html>' % (e(self.title), ''.join(nav)))
        ncx_doc = ('<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
                   'version="2005-1"><head><meta name="dtb:uid" content="%s"/></head><docTitle><text>%s</text>'
                   '</docTitle><navMap>%s</navMap></ncx>' % (e(self.ident), e(self.title), ''.join(ncx)))
        opf = ('<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
               'unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">%s</metadata>'
               '<manifest>%s<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
               '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
               '<spine toc="ncx">%s</spine></package>' % (''.join(meta), ''.join(items), ''.join(refs)))
        with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            z.writestr('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:'
                       'names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" '
                       'media-type="application/oebps-package+xml"/></rootfiles></container>')
            z.writestr('OEBPS/content.opf', opf)
            z.writestr('OEBPS/nav.xhtml', nav_doc)
            z.writestr('OEBPS/toc.ncx', ncx_doc)
            for path, data in self.files.items():
                z.writestr('OEBPS/' + path, data)


def tidy(markup, title, css_links=()):
    """Any HTML (KF8's XHTML, or MOBI 6's loose HTML) as a well-formed XHTML document."""
    try:
        root = etree.fromstring(markup, etree.XMLParser(recover=True, resolve_entities=False, huge_tree=True))
        if root is None or etree.QName(root).localname != 'html':
            raise ValueError
    except Exception:
        root = lhtml.document_fromstring(markup or b'<p></p>')
    for el in root.iter():
        if isinstance(el.tag, str) and (etree.QName(el).namespace in (None, XHTML)):
            el.tag = etree.QName(el).localname
    etree.cleanup_namespaces(root)
    head = root.find('head')
    if head is None:
        head = etree.Element('head')
        root.insert(0, head)
    if head.find('title') is None:
        etree.SubElement(head, 'title').text = title
    for href in css_links:
        etree.SubElement(head, 'link', rel='stylesheet', type='text/css', href=href)
    if root.find('body') is None:
        etree.SubElement(root, 'body')
    for el in root.iter():
        if isinstance(el.tag, str):
            for k in ('aid', 'filepos', 'recindex', 'hirecindex', 'lowrecindex', 'mediarecindex', 'height', 'width') \
                    if el.tag != 'img' else ('aid', 'recindex', 'hirecindex', 'lowrecindex', 'mediarecindex'):
                if k in el.attrib and (k in ('aid',) or el.tag not in ('table', 'td', 'th', 'col', 'img', 'svg')):
                    del el.attrib[k]
    raw = etree.tostring(root, encoding='utf-8')
    raw = re.sub(rb'^<html', b'<html xmlns="%s"' % XHTML.encode(), raw, count=1) \
        if b'xmlns="%s"' % XHTML.encode() not in raw[:300] else raw
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + raw


class Reader:
    def __init__(self, data):
        if len(data) < 78 or data[60:68] not in (b'BOOKMOBI', b'TEXtREAd'):
            raise ConversionError('Not a Kindle book.')
        n = u16(data, 76)
        offs = [u32(data, 78 + 8 * i) for i in range(n)] + [len(data)]
        self.records = [data[offs[i]:offs[i + 1]] for i in range(n)]
        self.plain = data[60:68] == b'TEXtREAd'
        self.mobi6 = Section(self.records, 0)
        self.kf8 = None
        if self.mobi6.encryption:
            raise DRMError('This book is locked with DRM.')
        if self.mobi6.version >= 8 and self.mobi6.skel != NULL:
            self.kf8 = self.mobi6
        else:
            k8 = self.mobi6.ex_int(121)
            if k8 and 0 < k8 < len(self.records) and self.records[k8 - 1] == b'BOUNDARY':
                self.kf8 = Section(self.records, k8)
        meta = self.kf8 or self.mobi6
        title = (self.mobi6.ex_str(503) or [self.mobi6.name or meta.name or 'Unknown'])[0]
        lang = (self.mobi6.ex_str(524) or [''])[0] or LANGS.get(self.mobi6.locale & 0xffff) or \
            LANGS.get(self.mobi6.locale & 0x3ff) or 'en'
        asin = (self.mobi6.ex_str(113) or [None])[0]
        self.out = Out(title, self.mobi6.ex_str(100), lang, (self.mobi6.ex_str(101) or [''])[0],
                       re.sub(r'<[^>]+>', '', (self.mobi6.ex_str(103) or [''])[0]), (self.mobi6.ex_str(106) or [''])[0],
                       ident='urn:uuid:%s' % asin if asin and re.match(r'^[0-9a-fA-F-]{36}$', asin) else None)

    def resources(self):
        def first(sec):
            fr = sec.first_resource
            return sec.base + fr if fr not in (NULL, 0) else sec.base + sec.text_count + 1
        if self.kf8 is None or self.kf8 is self.mobi6:
            return Resources(self.records, [(first(self.mobi6), len(self.records))])
        return Resources(self.records, [(first(self.mobi6), self.kf8.base - 1),
                                        (first(self.kf8), len(self.records))])

    def convert(self, out_path):
        if self.plain:
            self.from_plain()
        elif self.kf8 is not None:
            self.from_kf8()
        else:
            self.from_mobi6()
        if not self.out.spine:
            raise ConversionError('No text found in this book.')
        self.out.write(out_path)

    def add_resources(self, res):
        for item in res.used():
            self.out.add(item[0], item[1], item[2])
        cov = self.mobi6.ex_int(201)
        if cov is not None and res.get(cov + 1):
            self.out.cover = res.get(cov + 1)[0]

    # plain PalmDOC

    def from_plain(self):
        text = self.mobi6.text().decode('cp1252', 'replace')
        paras = ''.join('<p>%s</p>' % html.escape(p.strip()) for p in re.split(r'\n\s*\n|\r\n', text) if p.strip())
        self.out.add('text/part0001.xhtml', tidy(('<html><body>%s</body></html>' % paras).encode('utf-8'),
                                                 self.out.title), 'application/xhtml+xml')
        self.out.spine.append('text/part0001.xhtml')

    # MOBI 6

    def from_mobi6(self):
        sec = self.mobi6
        raw = sec.text()
        res = self.resources()
        self.add_resources(res)
        # anchors where filepos links and the table of contents point
        targets = {int(m) for m in re.findall(rb'filepos=["\']?0*(\d+)', raw)}
        toc = []
        entries, string = read_index(sec, sec.ncx)
        for v, (label, pos), depth in toc_order([(v, (string(v[3][0]) if 3 in v else '', v.get(1, [0])[0]))
                                                   for key, v in entries]):
            toc.append((label or '•', pos, depth))
            targets.add(pos)
        out = bytearray(raw)
        for pos in sorted(targets, reverse=True):
            if pos > len(out):
                continue
            # not inside a tag
            lt, gt = out.rfind(b'<', 0, pos), out.rfind(b'>', 0, pos)
            p = lt if lt > gt else pos
            out[p:p] = b'<a id="filepos%d"></a>' % pos
        raw = bytes(out)
        raw = re.sub(rb'<a([^>]*?)\sfilepos=["\']?0*(\d+)["\']?', lambda m: b'<a%s href="#filepos%s"' % (m.group(1), m.group(2)), raw)

        def img(m):
            item = res.get(int(m.group(2)))
            return (b' src="../%s"' % item[0].encode()) if item else b''
        raw = re.sub(rb'\s(?:hi|low)?recindex=(["\']?)0*(\d+)\1', img, raw)
        raw = re.sub(rb'(?is)<guide>.*?</guide>', b'', raw)
        enc = sec.encoding
        parts = re.split(rb'(?i)<mbp:pagebreak\s*/?>', raw)
        raw = re.sub(rb'</?mbp:[^>]*>', b'', raw)
        files, idmap = [], {}
        for i, part in enumerate(parts):
            part = re.sub(rb'(?i)</?mbp:[^>]*>', b'', part)
            part = re.sub(rb'(?is)</?(html|head|body)\b[^>]*>', b'', part)
            if not re.sub(rb'<[^>]+>|\s|&nbsp;', b'', part) and b'<img' not in part and not re.search(rb'id="filepos', part):
                continue
            text = part.decode(enc, 'replace')
            path = 'text/part%04d.xhtml' % (len(files) + 1)
            for a in re.findall(r'id="(filepos\d+)"', text):
                idmap[a] = path
            files.append((path, text))
        for path, text in files:
            def link(m):
                target = m.group(1)
                where = idmap.get(target)
                if not where:
                    return 'href="#"'
                return 'href="%s#%s"' % ('' if where == path else posixpath.basename(where), target)
            text = re.sub(r'href="#(filepos\d+)"', link, text)
            doc = tidy(('<html><head><meta charset="utf-8"/></head><body>%s</body></html>' % text).encode('utf-8'),
                       self.out.title)
            self.out.add(path, doc, 'application/xhtml+xml')
            self.out.spine.append(path)
        for label, pos, depth in toc:
            a = 'filepos%d' % pos
            if a in idmap:
                self.out.toc.append((label, '%s#%s' % (idmap[a], a), depth))

    # KF8

    def from_kf8(self):
        sec = self.kf8
        text = sec.text()
        res = self.resources()
        self.add_resources(res)
        flows = [text]
        if sec.fdst not in (NULL, None):
            f = sec.rec(sec.fdst)
            if f[:4] == b'FDST':
                n = u32(f, 8)
                bounds = struct.unpack_from('>%dI' % (2 * n), f, 12)
                flows = [text[bounds[2 * i]:bounds[2 * i + 1]] for i in range(n)]
        html0 = flows[0]
        skels, _ = read_index(sec, sec.skel)
        frags, string = read_index(sec, sec.frag)
        files = []          # (start in html0 as rebuilt, rebuilt bytes, [(fragment number, insert pos)])
        fi = 0
        for key, v in skels:
            count = v.get(1, [0])[0]
            start, length = v[6][0], v[6][1]
            doc = html0[start:start + length]
            base = start + length
            placed = []
            for _ in range(count):
                if fi >= len(frags):
                    break
                fkey, fv = frags[fi]
                ins = int(fkey) - start
                flen = fv[6][1]
                data = html0[base:base + flen]
                # some writers point one tag too early or late; keep the fragment out of the middle of a tag
                if 0 <= ins <= len(doc):
                    doc = doc[:ins] + data + doc[ins:]
                else:
                    doc += data
                placed.append((fi, int(fkey)))
                base += flen
                fi += 1
            files.append([start, doc, placed])
        if not files:
            files = [[0, html0, []]]
        frag_pos = {}       # fragment number -> absolute rebuilt position
        for start, doc, placed in files:
            for n, pos in placed:
                frag_pos[n] = pos
        names = ['text/part%04d.xhtml' % (i + 1) for i in range(len(files))]

        def locate(fid, off):
            pos = frag_pos.get(fid, 0) + off
            for i in range(len(files) - 1, -1, -1):
                if files[i][0] <= pos:
                    return i, pos - files[i][0]
            return 0, 0
        # every place links and the table of contents point at
        want = {}
        pos_re = re.compile(rb'kindle:pos:fid:([0-9A-V]{4}):off:([0-9A-V]{10})')
        for f in files:
            for m in pos_re.finditer(f[1]):
                want.setdefault(locate(int(m.group(1), 32), int(m.group(2), 32)), None)
        ncx, ncx_string = read_index(sec, sec.ncx)
        toc_points = []
        for v, label, depth in toc_order([(v, ncx_string(v[3][0]) if 3 in v else '') for key, v in ncx]):
            if 6 in v:
                loc = locate(v[6][0], v[6][1])
                want.setdefault(loc, None)
                toc_points.append((label, loc, depth))
        # give each target element an id
        anchors = {}
        for i, f in enumerate(files):
            doc = f[1]
            for (fi_, off) in sorted((k for k in want if k[0] == i), key=lambda k: -k[1]):
                lt = doc.find(b'<', off)
                if lt < 0 or (doc[lt + 1:lt + 2] in (b'/', b'!', b'?')):
                    lt = doc.rfind(b'<', 0, max(off, 1))
                    while lt > 0 and doc[lt + 1:lt + 2] in (b'/', b'!', b'?'):
                        lt = doc.rfind(b'<', 0, lt)
                if lt < 0:
                    anchors[(fi_, off)] = ''
                    continue
                gt = doc.find(b'>', lt)
                tag = doc[lt:gt]
                m = re.search(rb'\sid=["\']([^"\']+)["\']', tag)
                if m:
                    anchors[(fi_, off)] = m.group(1).decode('utf-8', 'replace')
                    continue
                aid = 'pos%d_%d' % (i, off)
                name_end = re.match(rb'<[^\s>/]+', tag).end()
                doc = doc[:lt + name_end] + b' id="%s"' % aid.encode() + doc[lt + name_end:]
                anchors[(fi_, off)] = aid
            f[1] = doc

        def href_for(loc, here):
            i, _ = loc
            a = anchors.get(loc, '')
            name = posixpath.basename(names[i])
            return ('' if i == here else name) + ('#' + a if a else '') or name

        def embed(m, prefix):
            item = res.get(int(m.group(1), 32))
            return (prefix + item[0]).encode() if item else b''

        css_names = {}
        for n, flow in enumerate(flows[1:], 1):
            if flow.lstrip()[:4] in (b'<svg', b'<?xm') and b'<svg' in flow[:500]:
                css_names[n] = ('images/flow%04d.svg' % n, 'image/svg+xml')
            else:
                css_names[n] = ('styles/flow%04d.css' % n, 'text/css')
        for n, flow in enumerate(flows[1:], 1):
            path, mt = css_names[n]
            flow = re.sub(rb'kindle:embed:([0-9A-V]{4})(\?mime=[^\s"\')]*)?', lambda m: embed(m, '../'), flow)
            self.out.add(path, flow, mt)
        for i, (start, doc, placed) in enumerate(files):
            doc = pos_re.sub(lambda m: href_for(locate(int(m.group(1), 32), int(m.group(2), 32)), i).encode(), doc)
            doc = re.sub(rb'kindle:embed:([0-9A-V]{4})(\?mime=[^\s"\')]*)?', lambda m: embed(m, '../'), doc)

            def flowref(m):
                k = int(m.group(1), 32)
                return ('../' + css_names[k][0]).encode() if k in css_names else b''
            doc = re.sub(rb'kindle:flow:([0-9A-V]{4})(\?mime=[^\s"\')]*)?', flowref, doc)
            self.out.add(names[i], tidy(doc, self.out.title), 'application/xhtml+xml')
            self.out.spine.append(names[i])
        for label, loc, depth in toc_points:
            a = anchors.get(loc, '')
            self.out.toc.append((label or '•', names[loc[0]] + ('#' + a if a else ''), depth))


def to_epub(path, out_path):
    with open(path, 'rb') as f:
        data = f.read()
    try:
        Reader(data).convert(out_path)
    except (DRMError, ConversionError):
        raise
    except Exception as e:
        raise ConversionError("This Kindle book couldn't be converted (%s)." % e)
