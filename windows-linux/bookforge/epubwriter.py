"""Writes the rebuilt book as an EPUB 3 (a port of the Mac app's EPUBWriter.swift). Page 1 of the PDF is the cover."""
import datetime, uuid, zipfile

from .library import render_page

LABELS = {
    'id': ('Sampul', 'Sampul Belakang', 'Daftar Isi', 'Pembuka', 'Halaman %d'),
    'ja': ('表紙', '裏表紙', '目次', 'はじめに', '%dページ'),
}
DEFAULT_LABELS = ('Cover', 'Back Cover', 'Contents', 'Opening', 'Page %d')

CSS = """body { font-family: serif; line-height: 1.45; margin: 0 0.4em; }
p { margin: 0; text-indent: 1.4em; text-align: justify; }
p.noindent, p.first { text-indent: 0; }
p.spaced { text-indent: 0; margin-top: 0.8em; }
h2.chapter { font-weight: normal; text-align: center; page-break-before: always; font-size: 1.4em; letter-spacing: 0.05em; margin: 2.5em 0 1.8em; }
.dropcap { font-size: 1.9em; line-height: 1; font-weight: bold; }
p.scene { text-indent: 0; text-align: center; margin: 1em 0; }
.centered { margin: 1em 0; }
.centered p { text-indent: 0; text-align: center; }
.pageimg { text-align: center; }
.pageimg img { max-width: 100%; max-height: 100%; }
body.imagepage { margin: 0; }"""


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def write(blocks, body_end, pdf, title, author, language, out_path, publisher=''):
    lang = language
    cover_l, back_l, contents_l, opening_l, page_l = LABELS.get(lang, DEFAULT_LABELS)
    files = {}          # archive path -> bytes

    def page(t, body, cls=''):
        return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="%s" lang="%s">\n'
                '<head><meta charset="utf-8"/><title>%s</title><link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
                '<body%s>\n%s\n</body></html>' % (lang, lang, esc(t), ' class="%s"' % cls if cls else '', body))

    items = []          # dicts: id, file, title, xhtml, toc
    images = []         # (file, id)

    def add_image_page(i, t, toc, is_cover=False):
        img = render_page(pdf, i, 200 if is_cover else 150)
        if not img:
            return
        f = 'cover.jpg' if is_cover else 'page%03d.jpg' % i
        files['OEBPS/images/' + f] = img
        images.append((f, 'cover-image' if is_cover else 'img%d' % i))
        item_id = 'cover' if is_cover else 'p%03d' % i
        items.append(dict(id=item_id, file=item_id + '.xhtml', title=t, toc=toc,
                          xhtml=page(t, '<div class="pageimg"><img src="images/%s" alt="%s"/></div>' % (f, esc(t)), 'imagepage')))

    add_image_page(0, cover_l, False, is_cover=True)

    # split blocks into sections
    sections = []       # [title, [html]]
    after_break = True
    trailing, leading, inline = [], [], set()

    def ensure(t):
        if not sections:
            sections.append([t, []])
    for b in blocks:
        kind = b[0]
        if kind == 'image':
            i = b[1]
            if not sections:
                leading.append(i)
            elif i > body_end:
                trailing.append(i)
            else:
                sections[-1][1].append('<div class="pageimg"><img src="images/page%03d.jpg" alt=""/></div>' % i)
                inline.add(i)
        elif kind == 'heading':
            sections.append([b[1], ['<h2 class="chapter">%s</h2>' % esc(b[1])]])
            after_break = True
        elif kind == 'scene':
            ensure(opening_l)
            sections[-1][1].append('<p class="scene">* * *</p>')
            after_break = True
        elif kind == 'centered':
            ensure(opening_l)
            sections[-1][1].append('<div class="centered">' + ''.join('<p>%s</p>' % esc(l) for l in b[1]) + '</div>')
            after_break = True
        elif kind == 'para':
            _, text, first, spaced = b
            ensure(opening_l)
            cls = []
            if first:
                cls.append('first')
            elif after_break:
                cls.append('noindent')
            if spaced:
                cls.append('spaced')
            body = esc(text)
            if first and body:
                body = '<span class="dropcap">%s</span>%s' % (body[0], body[1:])
            sections[-1][1].append('<p%s>%s</p>' % (' class="%s"' % ' '.join(cls) if cls else '', body))
            after_break = False

    for i in leading:
        add_image_page(i, page_l % (i + 1), False)
    nav_index = len(items)
    for n, (t, html) in enumerate(sections):
        fid = 'ch%02d' % (n + 1)
        items.append(dict(id=fid, file=fid + '.xhtml', title=t, toc=True, xhtml=page(t, '\n'.join(html))))
    for i in sorted(inline):
        img = render_page(pdf, i, 150)
        if img:
            files['OEBPS/images/page%03d.jpg' % i] = img
            images.append(('page%03d.jpg' % i, 'img%d' % i))
    for k, i in enumerate(trailing):
        last = k == len(trailing) - 1
        add_image_page(i, back_l if last else page_l % (i + 1), k == 0 or last)

    # table of contents
    toc = [it for it in items if it['toc']]
    first_body = next((it['file'] for it in items if it['id'].startswith('ch')), toc[0]['file'] if toc else 'cover.xhtml')
    nav = page(contents_l,
               '<nav epub:type="toc" id="toc"><h2 class="chapter">%s</h2><ol>' % contents_l
               + ''.join('<li><a href="%s">%s</a></li>' % (it['file'], esc(it['title'])) for it in toc)
               + '</ol></nav><nav epub:type="landmarks" hidden=""><ol><li><a epub:type="cover" href="cover.xhtml">Cover</a></li>'
               + '<li><a epub:type="bodymatter" href="%s">Start</a></li></ol></nav>' % first_body)
    items.insert(nav_index, dict(id='nav', file='nav.xhtml', title='TOC', toc=False, xhtml=nav))

    book_id = 'urn:uuid:%s' % uuid.uuid4()
    ncx = ('<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           '<head><meta name="dtb:uid" content="%s"/></head>\n<docTitle><text>%s</text></docTitle><navMap>' % (book_id, esc(title))
           + ''.join('<navPoint id="np%d" playOrder="%d"><navLabel><text>%s</text></navLabel><content src="%s"/></navPoint>'
                     % (n + 1, n + 1, esc(it['title']), it['file']) for n, it in enumerate(toc))
           + '</navMap></ncx>')
    manifest = ''.join('<item id="%s" href="%s" media-type="application/xhtml+xml"%s/>'
                       % (it['id'], it['file'], ' properties="nav"' if it['id'] == 'nav' else '') for it in items)
    manifest += '<item id="css" href="style.css" media-type="text/css"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    manifest += ''.join('<item id="%s" href="images/%s" media-type="image/jpeg"%s/>'
                        % (iid, f, ' properties="cover-image"' if iid == 'cover-image' else '') for f, iid in images)
    spine = ''.join('<itemref idref="%s"/>' % it['id'] for it in items)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="%s">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
           '<dc:identifier id="bookid">%s</dc:identifier>\n<dc:title>%s</dc:title>\n<dc:creator>%s</dc:creator>\n%s'
           '<dc:language>%s</dc:language>\n<meta property="dcterms:modified">%s</meta>\n<meta name="cover" content="cover-image"/>\n'
           '</metadata>\n<manifest>%s</manifest>\n<spine toc="ncx">%s</spine>\n'
           '<guide><reference type="cover" title="Cover" href="cover.xhtml"/></guide>\n</package>'
           % (lang, book_id, esc(title), esc(author), '<dc:publisher>%s</dc:publisher>\n' % esc(publisher) if publisher else '',
              lang, stamp, manifest, spine))

    files['OEBPS/content.opf'] = opf.encode('utf-8')
    files['OEBPS/toc.ncx'] = ncx.encode('utf-8')
    files['OEBPS/style.css'] = CSS.encode('utf-8')
    for it in items:
        files['OEBPS/' + it['file']] = it['xhtml'].encode('utf-8')
    container = ('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    write_zip(out_path, [('META-INF/container.xml', container.encode('utf-8'))] + sorted(files.items()))


def write_zip(out_path, entries):
    """An EPUB zip: mimetype first and stored uncompressed, everything else deflated."""
    with zipfile.ZipFile(out_path, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        for name, data in entries:
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
