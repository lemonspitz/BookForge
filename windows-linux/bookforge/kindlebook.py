"""EPUB → AZW3 (Kindle Format 8), so EPUBs go into a Kindle's own library, with their covers, without Calibre.

    write(epub_path, out_path, asin=None, cover=None, title=None, authors=None)

An AZW3 file is a Palm database: record 0 holds the MOBI header and metadata (EXTH), then come the book's text
in 4 KB records, the indexes that say how the text is cut into files (skeletons and fragments) and the table of
contents, the images and fonts, and a few fixed records. The XHTML files are stored as skeletons (each file with
an empty <body>) followed by fragments of the body; links and images use kindle:pos, kindle:embed and
kindle:flow references.
"""
import io, posixpath, re, struct, time, random, zipfile, zlib
from urllib.parse import unquote, urlsplit
from xml.sax.saxutils import escape

from lxml import etree

XHTML = 'http://www.w3.org/1999/xhtml'
SVG = 'http://www.w3.org/2000/svg'
XLINK = 'http://www.w3.org/1999/xlink'
OPF = 'http://www.idpf.org/2007/opf'
DC = 'http://purl.org/dc/elements/1.1/'
NCX = 'http://www.daisy.org/z3986/2005/ncx/'
NULL = 0xffffffff
RECORD_SIZE = 4096
CHUNK_SIZE = 8192
DIGITS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
IMAGE_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
               '.bmp': 'image/bmp', '.webp': 'image/webp', '.svg': 'image/svg+xml'}
FONT_TYPES = {'.ttf': 'font/ttf', '.otf': 'font/otf', '.woff': 'font/woff', '.woff2': 'font/woff2'}

# Windows language ids, which the MOBI header uses
LOCALES = {'en': 0x09, 'id': 0x21, 'ja': 0x11, 'ko': 0x12, 'zh': 0x04, 'zh-hans': 0x804, 'zh-cn': 0x804,
           'zh-hant': 0x404, 'zh-tw': 0x404, 'zh-hk': 0xc04, 'th': 0x1e, 'vi': 0x2a, 'fr': 0x0c, 'de': 0x07,
           'es': 0x0a, 'it': 0x10, 'pt': 0x16, 'nl': 0x13, 'ru': 0x19, 'pl': 0x15, 'tr': 0x1f, 'ar': 0x01}


class ConversionError(Exception):
    pass


# MARK: - numbers and blocks

def base32(n, width):
    s = ''
    while n:
        s = DIGITS[n % 32] + s
        n //= 32
    return s.rjust(width, '0')


def encint(v):
    """Forward variable-width integer: 7 bits a byte, the last byte flagged with its high bit."""
    out = [v & 0x7f]
    v >>= 7
    while v:
        out.insert(0, v & 0x7f)
        v >>= 7
    out[-1] |= 0x80
    return bytes(out)


def align(b):
    return b + b'\0' * (-len(b) % 4)


def locale(lang):
    lang = (lang or 'en').lower().replace('_', '-')
    return LOCALES.get(lang) or LOCALES.get(lang.split('-')[0], 0)


# MARK: - indexes (INDX records)

class CNCX:
    """The string table index entries point into: each string is its length then UTF-8, in 64 KB records."""
    def __init__(self, strings=()):
        self.offsets, self.records, self._cur = {}, [], b''
        for s in strings:
            self.add(s)

    def add(self, s):
        if s in self.offsets:
            return self.offsets[s]
        raw = s.encode('utf-8')[:500]
        item = encint(len(raw)) + raw
        if len(self._cur) + len(item) > 0xfbf8:
            self.records.append(align(self._cur))
            self._cur = b''
        self.offsets[s] = len(self.records) * 0x10000 + len(self._cur)
        self._cur += item
        return self.offsets[s]

    def finish(self):
        if self._cur:
            self.records.append(align(self._cur))
            self._cur = b''
        return self.records


def build_index(entries, tags, cncx_records=()):
    """INDX records: a header record (with the tag table), entry records, then the CNCX string records.
    tags: [(tag, values per entry, mask)]; entries: [(key str, {tag: [values]})]."""
    def control_byte(values):
        cb = 0
        for tag, vpe, mask in tags:
            n = len(values.get(tag, ())) // vpe
            shift = (mask & -mask).bit_length() - 1
            cb |= mask & (n << shift)
        return cb

    limit = 0x10000 - 192 - 1048
    blocks = [[b'', b'', 0, b'']]       # entries, idxt, count, last key
    for key, values in entries:
        k = key.encode('ascii')
        raw = bytes([len(k)]) + k + bytes([control_byte(values)])
        for tag, vpe, mask in tags:
            for v in values.get(tag, ()):
                raw += encint(v)
        blk = blocks[-1]
        if len(blk[0]) + len(blk[1]) + len(raw) + 2 > limit:
            blocks.append([b'', b'', 0, b''])
            blk = blocks[-1]
        blk[1] += struct.pack('>H', 192 + len(blk[0]))
        blk[0] += raw
        blk[2] += 1
        blk[3] = k
    records = []
    for body, idxt, count, _ in blocks:
        body = align(body)
        head = (b'INDX' + struct.pack('>IIIIII', 192, 0, 1, 0, 192 + len(body), count) + b'\xff' * 8 + b'\0' * 156)
        records.append(head + body + align(b'IDXT' + idxt))
    tagx = b'TAGX' + struct.pack('>II', 12 + 4 * (len(tags) + 1), 1)
    for tag, vpe, mask in tags:
        tagx += bytes([tag, vpe, mask, 0])
    tagx += bytes([0, 0, 0, 1])
    geometry, idxt, pos = b'', b'IDXT', 192 + len(tagx)
    for _, _, count, last in blocks:
        g = bytes([len(last)]) + last + struct.pack('>H', count)
        idxt += struct.pack('>H', pos)
        geometry += g
        pos += len(g)
    geometry = align(geometry)
    header = struct.pack('>4sIIIIIIIIIIIII', b'INDX', 192, 0, 0, 2, 192 + len(tagx) + len(geometry), len(records),
                         65001, NULL, sum(b[2] for b in blocks), 0, 0, 0, len(cncx_records))
    header += b'\0' * (180 - len(header)) + struct.pack('>I', 192) + b'\0' * 8
    return [header + tagx + geometry + align(idxt)] + records + list(cncx_records)


# MARK: - reading the EPUB

class Epub:
    def __init__(self, path):
        self.zf = zipfile.ZipFile(path)
        self.names = set(self.zf.namelist())
        container = etree.fromstring(self.zf.read('META-INF/container.xml'))
        opf_path = container.xpath('//*[local-name()="rootfile"]/@full-path')[0]
        self.opf_dir = posixpath.dirname(opf_path)
        opf = etree.fromstring(self.zf.read(opf_path))
        ns = {'o': OPF, 'dc': DC}

        def dc(name):
            return [(e.text or '').strip() for e in opf.xpath('//dc:%s' % name, namespaces=ns) if (e.text or '').strip()]
        self.title = (dc('title') or ['Unknown'])[0]
        self.authors = dc('creator')
        self.language = (dc('language') or ['en'])[0]
        self.publisher = (dc('publisher') or [''])[0]
        self.description = re.sub(r'<[^>]+>', '', (dc('description') or [''])[0])
        self.date = (dc('date') or [''])[0]
        self.subjects = dc('subject')
        self.manifest = {}          # id -> (path in zip, media type, properties)
        for it in opf.xpath('//o:manifest/o:item', namespaces=ns):
            self.manifest[it.get('id')] = (self.join(self.opf_dir, it.get('href', '')), it.get('media-type', ''),
                                           it.get('properties', ''))
        spine = opf.xpath('//o:spine', namespaces=ns)
        self.direction = spine[0].get('page-progression-direction') if spine else None
        self.spine = []
        for ref in opf.xpath('//o:spine/o:itemref', namespaces=ns):
            m = self.manifest.get(ref.get('idref'))
            if m and m[0] in self.names and ('html' in m[1] or m[0].lower().endswith(('.html', '.htm', '.xhtml'))):
                if m[0] not in self.spine:
                    self.spine.append(m[0])
        if not self.spine:
            raise ConversionError('This EPUB has no pages to convert.')
        self.cover = self._cover(opf, ns)
        self.toc = self._toc(opf, ns, spine[0].get('toc') if spine else None)

    @staticmethod
    def join(base, href):
        href = unquote(urlsplit(href).path)
        return posixpath.normpath(posixpath.join(base, href)).lstrip('/') if href else ''

    def read(self, path):
        return self.zf.read(path)

    def _cover(self, opf, ns):
        for path, mt, props in self.manifest.values():
            if 'cover-image' in props.split() and path in self.names:
                return path
        ids = opf.xpath('//o:meta[@name="cover"]/@content', namespaces=ns)
        if ids and ids[0] in self.manifest and self.manifest[ids[0]][1].startswith('image/'):
            return self.manifest[ids[0]][0]
        for path, mt, props in self.manifest.values():
            if mt.startswith('image/') and 'cover' in posixpath.basename(path).lower():
                return path
        return None

    def _toc(self, opf, ns, ncx_id):
        """[(label, path, fragment, depth)] from the EPUB 3 nav document, else the NCX."""
        nav = next((p for p, mt, props in self.manifest.values() if 'nav' in props.split()), None)
        out = []
        if nav and nav in self.names:
            try:
                doc = parse_html(self.read(nav))
                navs = [n for n in doc.iter('{*}nav', 'nav') if 'toc' in (n.get('{http://www.idpf.org/2007/ops}type') or
                                                                       n.get('epub:type') or n.get('type') or '')]
                if not navs:
                    navs = list(doc.iter('{*}nav', 'nav'))[:1]

                def walk(ol, depth):
                    for li in ol:
                        if not isinstance(li.tag, str) or etree.QName(li).localname != 'li':
                            continue
                        a = next((x for x in li.iter() if isinstance(x.tag, str) and etree.QName(x).localname in ('a', 'span')), None)
                        if a is not None and a.get('href'):
                            label = ' '.join(''.join(a.itertext()).split())
                            path = self.join(posixpath.dirname(nav), a.get('href'))
                            out.append((label or '•', path, urlsplit(a.get('href')).fragment, depth))
                        for sub in li:
                            if isinstance(sub.tag, str) and etree.QName(sub).localname == 'ol':
                                walk(sub, depth + 1)
                for n in navs:
                    for ol in n:
                        if isinstance(ol.tag, str) and etree.QName(ol).localname == 'ol':
                            walk(ol, 0)
            except Exception:
                out = []
        if out:
            return out
        ncx = self.manifest.get(ncx_id, (None,))[0] if ncx_id else None
        if not ncx:
            ncx = next((p for p, mt, _ in self.manifest.values() if mt == 'application/x-dtbncx+xml'), None)
        if ncx and ncx in self.names:
            try:
                doc = etree.fromstring(self.read(ncx))

                def walk(parent, depth):
                    for np in parent.findall('{%s}navPoint' % NCX):
                        label = ' '.join(''.join(np.find('{%s}navLabel' % NCX).itertext()).split()) \
                            if np.find('{%s}navLabel' % NCX) is not None else ''
                        c = np.find('{%s}content' % NCX)
                        if c is not None and c.get('src'):
                            out.append((label or '•', self.join(posixpath.dirname(ncx), c.get('src')),
                                        urlsplit(c.get('src')).fragment, depth))
                        walk(np, depth + 1)
                nm = doc.find('{%s}navMap' % NCX)
                if nm is not None:
                    walk(nm, 0)
            except Exception:
                pass
        return out


def parse_html(raw):
    """XHTML as an lxml tree; tolerant of the HTML some EPUBs contain."""
    raw = re.sub(rb'^\s*<\?xml[^>]*\?>', b'', raw.lstrip(b'\xef\xbb\xbf'))
    try:
        return etree.fromstring(raw, etree.XMLParser(recover=True, resolve_entities=False, huge_tree=True))
    except Exception:
        from lxml import html
        return html.fromstring(raw)


# MARK: - the converter

class Writer:
    def __init__(self, epub, asin, cover, title, authors):
        self.epub = epub
        self.asin = asin
        self.title = title or epub.title
        self.authors = authors or epub.authors
        self.resources = []         # [(bytes, mime)] in kindle:embed order (1-based)
        self.res_index = {}         # zip path -> 1-based index
        self.flows = [b'']          # flow 0 is the XHTML; then style sheets
        self.flow_index = {}        # css zip path -> flow number
        self.aid = 0
        self.targets = {}           # (path, fragment) -> aid
        self.links = []             # placeholder number -> (path, fragment)
        self.cover_override = cover
        self.vertical = False

    def next_aid(self):
        self.aid += 1
        return base32(self.aid, 1)

    # resources

    def resource(self, path):
        if path in self.res_index:
            return self.res_index[path]
        if path not in self.epub.names:
            return None
        data = self.epub.read(path)
        ext = posixpath.splitext(path)[1].lower()
        if ext in FONT_TYPES:
            # FONT record: size, flags (1 = zlib), data offset, no obfuscation
            packed = zlib.compress(data, 9)
            rec = b'FONT' + struct.pack('>IIIII', len(data), 1, 24, 0, 0) + packed
            mime = FONT_TYPES[ext]
        else:
            rec, mime = image_record(data, ext)
            if rec is None:
                return None
        self.resources.append((rec, mime))
        self.res_index[path] = len(self.resources)
        return self.res_index[path]

    def embed(self, path):
        i = self.resource(path)
        if i is None:
            return None
        return 'kindle:embed:%s?mime=%s' % (base32(i, 4), self.resources[i - 1][1])

    def css(self, raw, base):
        """A style sheet with its url()s pointing into the book; @import and remote fonts dropped."""
        text = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else raw
        text = re.sub(r'@import[^;]*;', '', text)
        if re.search(r'writing-mode\s*:\s*vertical-rl', text):
            self.vertical = True

        def url(m):
            href = m.group(2).strip()
            if re.match(r'^[a-z][a-z0-9+.-]*:', href, re.I):
                return m.group(0)
            e = self.embed(Epub.join(base, href))
            return 'url(%s)' % e if e else 'url()'
        return re.sub(r'url\(\s*([\'"]?)([^\'")]+)\1\s*\)', url, text)

    def flow(self, path):
        if path not in self.flow_index:
            if path not in self.epub.names:
                return None
            self.flows.append(self.css(self.epub.read(path), posixpath.dirname(path)).encode('utf-8'))
            self.flow_index[path] = len(self.flows) - 1
        return 'kindle:flow:%s?mime=text/css' % base32(self.flow_index[path], 4)

    # documents

    def prepare(self, path):
        """The document cleaned for the Kindle, with aids, kindle: references and link placeholders."""
        root = parse_html(self.epub.read(path))
        base = posixpath.dirname(path)
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            q = etree.QName(el)
            if q.namespace in (XHTML, None):
                el.tag = q.localname
            for k in list(el.attrib):
                kq = etree.QName(k)
                if kq.namespace and kq.namespace not in (XLINK, 'http://www.w3.org/XML/1998/namespace'):
                    del el.attrib[k]
        etree.cleanup_namespaces(root)
        for el in list(root.iter('script', 'noscript', '{%s}script' % SVG)):
            el.getparent().remove(el) if el.getparent() is not None else None
        if root.tag != 'html':
            raise ConversionError('%s is not an XHTML page.' % path)
        body = root.find('body')
        if body is None:
            body = etree.SubElement(root, 'body')
        head = root.find('head')
        if head is None:
            head = etree.Element('head')
            root.insert(0, head)
        if head.find('title') is None:
            etree.SubElement(head, 'title').text = self.title
        for link in list(head.iter('link')):
            if 'stylesheet' in (link.get('rel') or '').lower() and link.get('href'):
                ref = self.flow(Epub.join(base, link.get('href')))
                if ref:
                    link.attrib.clear()
                    link.set('rel', 'stylesheet')
                    link.set('type', 'text/css')
                    link.set('href', ref)
                    continue
            head.remove(link)
        for st in root.iter('style'):
            st.text = self.css(st.text or '', base)
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            if el.get('style') and 'url(' in el.get('style'):
                el.set('style', self.css(el.get('style'), base))
            if el.get('style') and 'vertical-rl' in el.get('style'):
                self.vertical = True
        for img in list(root.iter('img', '{%s}image' % SVG)):
            key = 'src' if img.tag == 'img' else ('{%s}href' % XLINK if img.get('{%s}href' % XLINK) else 'href')
            href = img.get(key)
            e = self.embed(Epub.join(base, href)) if href and not re.match(r'^[a-z]+:', href, re.I) else None
            if e:
                img.set(key, e)
            elif img.getparent() is not None and img.tag == 'img':
                img.getparent().remove(img)
        for a in root.iter('a'):
            href = a.get('href')
            if not href:
                continue
            if re.match(r'^[a-z][a-z0-9+.-]*:', href, re.I):
                continue
            target = (Epub.join(base, href) if urlsplit(href).path else path, urlsplit(href).fragment)
            self.links.append(target)
            a.set('href', 'kindle:pos:fid:0000:off:%010d' % (len(self.links) - 1))
        # anchors: the body, its children and everything with an id
        body.set('aid', self.next_aid())
        self.targets[(path, '')] = body.get('aid')
        for el in body.iter():
            if not isinstance(el.tag, str) or el is body:
                continue
            if el.get('id') or el.getparent() is body:
                if etree.QName(el).namespace == SVG and el.tag != '{%s}svg' % SVG:
                    continue
                el.set('aid', self.next_aid())
                if el.get('id'):
                    self.targets[(path, el.get('id'))] = el.get('aid')
        if body.get('id'):
            self.targets[(path, body.get('id'))] = body.get('aid')
        return root, body

    def split(self, root, body):
        """(skeleton bytes, insert offset in it, [fragment bytes])."""
        pieces = [escape(body.text).encode('utf-8')] if body.text else []
        for child in body:
            pieces.append(serialize(child, with_tail=True))
        body.text = None
        for child in list(body):
            body.remove(child)
        skel = serialize(root)
        skel = re.sub(rb'^<html', b'<html xmlns="%s"' % XHTML.encode(), skel, count=1) \
            if b'xmlns="%s"' % XHTML.encode() not in skel[:300] else skel
        skel = b'<?xml version="1.0" encoding="utf-8"?>\n' + skel
        insert = skel.rfind(b'</body>')
        chunks, cur = [], b''
        for p in pieces:
            if cur and len(cur) + len(p) > CHUNK_SIZE:
                chunks.append(cur)
                cur = b''
            cur += p
        if cur or not chunks:
            chunks.append(cur or b' ')
        return skel, insert, chunks

    def build(self):
        skels, frags = [], []           # (name, chunk count, start, length); (insert, selector, file, seq, start, length)
        text = b''
        docs = []
        for path in self.epub.spine:
            try:
                root, body = self.prepare(path)
            except ConversionError:
                continue
            docs.append((path, root, body))
        if not docs:
            raise ConversionError('No readable pages in this EPUB.')
        file_starts = {}
        seq = 0
        for n, (path, root, body) in enumerate(docs):
            body_aid = body.get('aid')
            skel, insert, chunks = self.split(root, body)
            start = len(text)
            file_starts[path] = start
            skels.append(('SKEL%010d' % n, len(chunks), start, len(skel)))
            pos, cp = start + insert, 0
            for c in chunks:
                frags.append([pos, "P-//*[@aid='%s']" % body_aid, n, seq, cp, len(c)])
                pos += len(c)
                cp += len(c)
                seq += 1
            text += skel + b''.join(chunks)
        # rebuild the files to find where each aid is (the placeholders keep their width)
        rebuilt = []
        for n, (name, count, start, length) in enumerate(skels):
            doc = text[start:start + length]
            base = start + length
            for f in (f for f in frags if f[2] == n):
                ins = f[0] - start
                doc = doc[:ins] + text[base:base + f[5]] + doc[ins:]
                base += f[5]
            rebuilt.append(doc)
        whole = b''.join(rebuilt)
        aid_pos = {}
        for m in re.finditer(rb'<[^>]+?\said="([0-9A-Z]+)"', whole):
            off = m.start()
            fid = None
            for f in frags:
                if f[0] <= off < f[0] + f[5]:
                    fid = (f[3], off - f[0])
                    break
                if f[0] > off:
                    fid = (f[3], 0)
                    break
            if fid is None:
                fid = (frags[-1][3], max(0, off - frags[-1][0]))
            aid_pos[m.group(1).decode()] = (fid, off)

        def where(target):
            aid = self.targets.get(target) or self.targets.get((target[0], ''))
            return aid_pos.get(aid) if aid else None

        def sub(m):
            w = where(self.links[int(m.group(1))])
            if not w:
                return b'kindle:pos:fid:0000:off:0000000000'
            (seqn, off), _ = w
            return ('kindle:pos:fid:%s:off:%s' % (base32(seqn, 4), base32(off, 10))).encode()
        text = re.sub(rb'kindle:pos:fid:0000:off:(\d{10})', sub, text)
        self.flows[0] = text

        # table of contents
        toc = []
        for label, path, frag, depth in self.epub.toc:
            w = where((path, frag))
            if w:
                toc.append(dict(label=label, depth=min(depth, 3), pos_fid=w[0], offset=w[1]))
        if not toc:
            for path, root, body in docs:
                w = where((path, ''))
                if w:
                    toc.append(dict(label=self.title if not toc else 'Part %d' % (len(toc) + 1), depth=0,
                                    pos_fid=w[0], offset=w[1]))
        return skels, frags, toc

    def ncx_index(self, toc, text_length):
        # parents: the nearest earlier entry one level up
        for i, e in enumerate(toc):
            e['id'] = i
            e['parent_id'] = None
            for j in range(i - 1, -1, -1):
                if toc[j]['depth'] < e['depth']:
                    e['depth'] = toc[j]['depth'] + 1
                    e['parent_id'] = j
                    break
            else:
                e['depth'] = 0
        entries = sorted(toc, key=lambda e: (e['depth'], e['offset']))
        index = {e['id']: k for k, e in enumerate(entries)}
        cncx = CNCX()
        width = max(2, len('%X' % max(len(entries) - 1, 0)))
        out = []
        for k, e in enumerate(entries):
            children = [index[c['id']] for c in toc if c['parent_id'] == e['id']]
            later = [x['offset'] for x in entries if x['depth'] <= e['depth'] and x['offset'] > e['offset']]
            vals = {1: [e['offset']], 2: [max(1, (min(later) if later else text_length) - e['offset'])],
                    3: [cncx.add(e['label'])], 4: [e['depth']], 6: list(e['pos_fid'])}
            if e['parent_id'] is not None:
                vals[21] = [index[e['parent_id']]]
            if children:
                vals[22], vals[23] = [min(children)], [max(children)]
            out.append(('%0*X' % (width, k), vals))
        tags = [(1, 1, 1), (2, 1, 2), (3, 1, 4), (4, 1, 8), (21, 1, 16), (22, 1, 32), (23, 1, 64), (6, 2, 128)]
        return build_index(out, tags, cncx.finish())

    def cover_resources(self):
        """(cover index, thumbnail index), both 0-based among the resources, or None."""
        data = self.cover_override
        if data is None and self.epub.cover:
            i = self.resource(self.epub.cover)
            if i is not None:
                cover = i - 1
                return cover, self.thumbnail(self.resources[cover][0], cover)
            return None, None
        if data is not None:
            rec, mime = image_record(data, '.jpg')
            if rec is None:
                return None, None
            self.resources.append((rec, mime))
            cover = len(self.resources) - 1
            return cover, self.thumbnail(rec, cover)
        return None, None

    def thumbnail(self, data, fallback):
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data)).convert('RGB')
            img.thumbnail((330, 470))
            buf = io.BytesIO()
            img.save(buf, 'JPEG', quality=85)
            self.resources.append((buf.getvalue(), 'image/jpeg'))
            return len(self.resources) - 1
        except Exception:
            return fallback

    def write(self, out):
        skels, frags, toc = self.build()
        cover, thumb = self.cover_resources()
        text = b''.join(self.flows)
        records = [b'']
        # text records, each with the bytes that complete its last character and their count
        size = 0
        for i in range(0, len(text), RECORD_SIZE):
            data = text[i:i + RECORD_SIZE]
            overlap = b''
            j = len(data) - 1
            while j >= 0 and j >= len(data) - 4 and (data[j] & 0xc0) == 0x80:
                j -= 1
            if j >= 0 and data[j] >= 0xc0:
                need = 2 if data[j] < 0xe0 else 3 if data[j] < 0xf0 else 4
                missing = need - (len(data) - j)
                if missing > 0:
                    overlap = text[i + RECORD_SIZE:i + RECORD_SIZE + missing]
            rec = data + overlap + bytes([len(overlap)])
            records.append(rec)
            size += len(rec)
        last_text = len(records) - 1
        if size % 4:
            records.append(b'\0' * (4 - size % 4))
        first_non_text = len(records)
        frag_cncx = CNCX(f[1] for f in frags)
        chunk_index = len(records)
        records += build_index([('%010d' % f[0], {2: [frag_cncx.offsets[f[1]]], 3: [f[2]], 4: [f[3]], 6: [f[4], f[5]]})
                                for f in frags], [(2, 1, 1), (3, 1, 2), (4, 1, 4), (6, 2, 8)], frag_cncx.finish())
        skel_index = len(records)
        records += build_index([(s[0], {1: [s[1], s[1]], 6: [s[2], s[3], s[2], s[3]]}) for s in skels],
                               [(1, 1, 3), (6, 2, 12)])
        ncx_index = NULL
        if toc:
            ncx_index = len(records)
            records += self.ncx_index(toc, len(self.flows[0]))
        first_resource = NULL
        if self.resources:
            first_resource = len(records)
            records += [r for r, _ in self.resources]
        fdst = len(records)
        bounds, start = [], 0
        for f in self.flows:
            bounds += [start, start + len(f)]
            start += len(f)
        records.append(b'FDST' + struct.pack('>II', 12, len(self.flows)) + struct.pack('>%dI' % len(bounds), *bounds))
        flis = len(records)
        records.append(b'FLIS\0\0\0\x08\0\x41\0\0\0\0\0\0\xff\xff\xff\xff\0\x01\0\x03\0\0\0\x03\0\0\0\x01' + b'\xff' * 4)
        fcis = len(records)
        records.append(b'FCIS\x00\x00\x00\x14\x00\x00\x00\x10\x00\x00\x00\x02\x00\x00\x00\x00' + struct.pack('>I', len(text))
                       + b'\x00\x00\x00\x00\x00\x00\x00\x28\x00\x00\x00\x00\x00\x00\x00'
                       + b'\x28\x00\x00\x00\x08\x00\x01\x00\x01\x00\x00\x00\x00')
        records.append(b'\xe9\x8e\r\n')

        records[0] = self.record0(len(text), last_text, first_non_text, first_resource, fdst, len(self.flows), fcis, flis,
                                  ncx_index, chunk_index, skel_index, cover, thumb)
        write_pdb(out, self.title, records)

    def exth(self, cover, thumb):
        recs = []

        def s(code, value):
            if value:
                recs.append(struct.pack('>II', code, 8 + len(value.encode('utf-8'))) + value.encode('utf-8'))

        def n(code, value):
            recs.append(struct.pack('>III', code, 12, value))
        for a in self.authors:
            s(100, a)
        s(101, self.epub.publisher)
        s(103, self.epub.description[:2000])
        for subj in self.epub.subjects[:10]:
            s(105, subj)
        s(106, self.epub.date)
        if self.asin:
            s(113, self.asin)
            s(504, self.asin)
        s(501, 'EBOK')
        s(503, self.title)
        s(524, self.epub.language)
        for code, value in ((204, 201), (205, 2), (206, 9), (207, 0)):
            n(code, value)
        s(535, '0730-890adc2')
        if cover is not None:
            n(201, cover)
            n(203, 0)
        if thumb is not None:
            n(202, thumb)
            s(129, 'kindle:embed:%s' % base32(thumb, 4))
        n(125, len(self.resources))
        n(131, 0)
        if self.vertical:
            s(525, 'vertical-rl')
        if self.epub.direction in ('rtl', 'ltr'):
            s(527, self.epub.direction)
        s(528, 'true')
        body = b''.join(recs)
        pad = b'\0' * (4 - len(body) % 4)
        return b'EXTH' + struct.pack('>II', len(body) + 12, len(recs)) + body + pad

    def record0(self, text_length, last_text, first_non_text, first_resource, fdst, fdst_count, fcis, flis,
                ncx_index, chunk_index, skel_index, cover, thumb):
        exth = self.exth(cover, thumb)
        title = self.title.encode('utf-8')
        title_offset = 16 + 264 + len(exth)
        h = struct.pack('>HHIHHHH', 1, 0, text_length, last_text, RECORD_SIZE, 0, 0)
        mobi = b'MOBI' + struct.pack('>IIIII', 264, 2, 65001, random.randint(0, 0xffffffff), 8)
        mobi += struct.pack('>II', NULL, NULL) + struct.pack('>8I', *([NULL] * 8))
        mobi += struct.pack('>III', first_non_text, title_offset, len(title))
        mobi += struct.pack('>IIII', locale(self.epub.language), 0, 0, 8)
        mobi += struct.pack('>I', first_resource) + b'\0' * 16
        mobi += struct.pack('>I', 0x50) + b'\0' * 32 + struct.pack('>I', NULL)
        mobi += struct.pack('>II', NULL, 0) + b'\0' * 8 + b'\0' * 8
        mobi += struct.pack('>IIIIII', fdst, fdst_count, fcis, 1, flis, 1)
        mobi += b'\0' * 8 + struct.pack('>II', NULL, 0) + b'\xff' * 8
        mobi += struct.pack('>IIIIII', 1, ncx_index, chunk_index, skel_index, NULL, NULL)
        mobi += struct.pack('>IIII', NULL, 0, NULL, 0)
        assert len(mobi) == 264, len(mobi)
        return h + mobi + exth + title + b'\0' * (2 + (-(len(title) + 2) % 4)) + b'\0' * 8192


def serialize(el, with_tail=False):
    raw = etree.tostring(el, encoding='utf-8', with_tail=with_tail)

    def fix(m):
        name = m.group(1).decode()
        if name.lower() in VOID or ':' in name:
            return m.group(0)
        return b'<%s%s></%s>' % (m.group(1), m.group(2), m.group(1))
    return re.sub(rb'<([A-Za-z][\w.-]*)((?:\s[^<>]*?)?)/>', fix, raw)


def image_record(data, ext):
    """An image the Kindle shows: JPEG, PNG and GIF as they are, anything else made into JPEG or PNG."""
    if data[:3] == b'\xff\xd8\xff':
        return data, 'image/jpeg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return data, 'image/png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return data, 'image/gif'
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        if img.mode in ('RGBA', 'LA', 'P'):
            img.save(buf, 'PNG')
            return buf.getvalue(), 'image/png'
        img.convert('RGB').save(buf, 'JPEG', quality=90)
        return buf.getvalue(), 'image/jpeg'
    except Exception:
        return None, None


def write_pdb(out, title, records):
    name = re.sub(r'[^A-Za-z0-9]+', '_', title).strip('_')[:31].encode('ascii') or b'book'
    now = int(time.time())
    n = len(records)
    head = name.ljust(32, b'\0') + struct.pack('>HHIIIIII', 0, 0, now, now, 0, 0, 0, 0) + b'BOOKMOBI'
    head += struct.pack('>IIH', 2 * n - 1, 0, n)
    offset = len(head) + 8 * n + 2
    table = b''
    for i, r in enumerate(records):
        table += struct.pack('>I', offset) + b'\0' + struct.pack('>I', 2 * i)[1:]
        offset += len(r)
    with open(out, 'wb') as f:
        f.write(head + table + b'\0\0')
        for r in records:
            f.write(r)


def write(epub_path, out_path, asin=None, cover=None, title=None, authors=None):
    """Converts an EPUB to AZW3. asin: the id the Kindle files the book (and its cover thumbnail) under;
    cover: JPEG/PNG bytes to use instead of the EPUB's own cover."""
    try:
        epub = Epub(epub_path)
    except ConversionError:
        raise
    except (zipfile.BadZipFile, KeyError, IndexError, etree.XMLSyntaxError) as e:
        raise ConversionError("This EPUB can't be read (%s)." % e)
    Writer(epub, asin, cover, title, authors).write(out_path)
