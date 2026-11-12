"""Checks the Kindle-book converters (no Calibre): EPUB -> AZW3 and AZW3/MOBI -> EPUB.

    python tests/kindle_check.py [path to the Mac app's BookForge binary]

Always checks the Windows/Linux converters (bookforge/kindlebook.py, kindleread.py). Given the Mac binary, it
also checks the Mac's (KindleFormat.swift) and cross-checks the two: each reads the other's AZW3 files. The
fixtures were made with Calibre from the same test book: an old-style MOBI, a joint MOBI (old and KF8) and an AZW3.
"""
import io, os, posixpath, re, subprocess, sys, tempfile, zipfile

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, '..'))
from lxml import etree  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from bookforge import kindlebook, kindleread  # noqa: E402

mac = sys.argv[1] if len(sys.argv) > 1 else None
tmp = tempfile.mkdtemp(prefix='bf-kindle-')
LABELS = ['Chapter One', 'Chapter Two', 'Middle', '第三章', 'Section two']


def make_epub(path):
    """The test book: cover, CSS with an image, a long chapter, CJK and other scripts, nested contents, links."""
    def jpeg(color, size, text):
        im = Image.new('RGB', size, color)
        ImageDraw.Draw(im).text((10, 10), text, fill='white')
        b = io.BytesIO()
        im.save(b, 'JPEG')
        return b.getvalue()
    png = io.BytesIO()
    Image.new('RGBA', (40, 40), (255, 0, 0, 128)).save(png, 'PNG')
    long = ''.join('<p id="p%d">Paragraph %d of the long chapter with “quotes” — é ü ñ. %s</p>\n' % (i, i, 'Words ' * 30)
                   for i in range(400))
    pages = {
        'text/ch1.xhtml': '<h1 id="c1">Chapter One</h1><p>It was a <i>dark</i> and <b>stormy</b> night. See '
                          '<a href="ch3.xhtml#s2">section two</a>.</p><p><img src="../images/pic.png" alt="pic"/></p>',
        'text/ch2.xhtml': '<h1>Chapter Two</h1>' + long + '<p><a href="#p5">Back to paragraph 5</a></p>',
        'text/ch3.xhtml': '<h1 id="c3">第三章 吾輩は猫である</h1><p>吾輩は猫である。名前はまだ無い。</p><h2 id="s2">Section two</h2>'
                          '<p>ภาษาไทย Tiếng Việt 한국어</p><br/><div></div>',
        'text/cover.xhtml': '<div><img src="../images/cover.jpg" alt="cover"/></div>',
    }

    def page(title, body):
        return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>%s</title><link rel="stylesheet" type="text/css" '
                'href="../styles/book.css"/></head><body epub:type="bodymatter">%s</body></html>' % (title, body))
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip')
        z.writestr('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:'
                   'opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="'
                   'application/oebps-package+xml"/></rootfiles></container>')
        z.writestr('OEBPS/images/cover.jpg', jpeg('navy', (600, 900), 'COVER'))
        z.writestr('OEBPS/images/pic.png', png.getvalue())
        z.writestr('OEBPS/images/bg.jpg', jpeg('green', (50, 50), 'b'))
        z.writestr('OEBPS/styles/book.css', 'body { font-family: serif; } h1 { background: url("../images/bg.jpg"); }')
        for k, v in pages.items():
            z.writestr('OEBPS/' + k, page(k, v))
        z.writestr('OEBPS/nav.xhtml', '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
                   'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>toc</title></head><body><nav epub:type="toc"><ol>'
                   '<li><a href="text/ch1.xhtml#c1">Chapter One</a></li><li><a href="text/ch2.xhtml">Chapter Two</a><ol><li>'
                   '<a href="text/ch2.xhtml#p200">Middle</a></li></ol></li><li><a href="text/ch3.xhtml#c3">第三章</a><ol><li>'
                   '<a href="text/ch3.xhtml#s2">Section two</a></li></ol></li></ol></nav></body></html>')
        z.writestr('OEBPS/content.opf', '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier '
                   'id="id">urn:uuid:1234</dc:identifier><dc:title>Test Book Ünïcode</dc:title><dc:creator>Ann Author'
                   '</dc:creator><dc:creator>Bob Writer</dc:creator><dc:language>en</dc:language></metadata><manifest>'
                   '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="cov" '
                   'href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/><item id="pic" href="images/'
                   'pic.png" media-type="image/png"/><item id="bg" href="images/bg.jpg" media-type="image/jpeg"/><item '
                   'id="css" href="styles/book.css" media-type="text/css"/><item id="cp" href="text/cover.xhtml" media-type'
                   '="application/xhtml+xml"/><item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/><item'
                   ' id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/><item id="c3" href="text/ch3.xhtml" '
                   'media-type="application/xhtml+xml"/></manifest><spine><itemref idref="cp"/><itemref idref="c1"/><itemref'
                   ' idref="c2"/><itemref idref="c3"/></spine></package>')


def check_epub(path, what, toc=True, nested=True):
    """Valid XHTML, every link and image resolves, the text and contents came through."""
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    assert z.namelist()[0] == 'mimetype', what + ': mimetype first'
    problems, ids = [], {}
    for n in names:
        if not n.endswith(('.xhtml', '.ncx', '.opf')):
            continue
        try:
            doc = etree.fromstring(z.read(n))
        except Exception as e:
            problems.append('%s: %s' % (n, e))
            continue
        for el in doc.iter():
            if not isinstance(el.tag, str):
                continue
            for attr in ('href', 'src', '{http://www.w3.org/1999/xlink}href'):
                v = el.get(attr)
                if not v or re.match(r'^[a-z]+:', v):
                    continue
                p, _, frag = v.partition('#')
                target = posixpath.normpath(posixpath.join(posixpath.dirname(n), p)) if p else n
                if target not in names:
                    problems.append('%s: missing %s' % (n, v))
                elif frag and target.endswith('.xhtml'):
                    if target not in ids:
                        ids[target] = set(etree.fromstring(z.read(target)).xpath('//@id'))
                    if frag not in ids[target]:
                        problems.append('%s: missing anchor %s' % (n, v))
    assert not problems, '%s: %s' % (what, problems[:5])
    text = ' '.join(re.sub(r'<[^>]+>', ' ', z.read(n).decode('utf-8')) for n in names if n.endswith('.xhtml') and 'nav' not in n)
    for s in ('Chapter One', 'stormy', 'Paragraph 399', '吾輩は猫である', 'Tiếng Việt'):
        assert s in text, '%s: "%s" missing' % (what, s)
    opf = z.read(next(n for n in names if n.endswith('.opf'))).decode('utf-8')
    assert 'Test Book Ünïcode' in opf and 'Ann Author' in opf, what + ': metadata'
    assert sum(n.startswith('OEBPS/images/') for n in names) >= 2, what + ': images'
    if toc:
        ncx = z.read(next(n for n in names if n.endswith('.ncx'))).decode('utf-8')
        labels = re.findall(r'<text>([^<]*)</text>', ncx)[1:]
        assert labels == LABELS, '%s: contents %s' % (what, labels)
        # old-style MOBI keeps no levels in its contents (Calibre writes them flat)
        assert not nested or re.search(r'Chapter Two</text></navLabel><content[^>]*/><navPoint[^>]*><navLabel><text>Middle', ncx), \
            what + ': "Middle" should be under "Chapter Two"'
    print('ok:', what)


def check_azw3(path, what):
    from bookforge.readers import meta
    with open(path, 'rb') as f:
        m = meta.mobi(f.read(65536))
    assert m and m.title == 'Test Book Ünïcode' and m.authors == ['Ann Author', 'Bob Writer'], what + ': header'
    assert m.asin == 'CHECK-ASIN' and m.cdetype == 'EBOK', what + ': ASIN %s %s' % (m.asin, m.cdetype)
    back = path + '.py.epub'
    kindleread.to_epub(path, back)
    check_epub(back, what + ' read back')


def run_mac(*args):
    r = subprocess.run([mac] + list(args), capture_output=True, text=True, timeout=300)
    print(r.stdout.strip(), r.stderr.strip()[-2000:])
    assert r.returncode == 0, 'Mac app failed: %s' % ' '.join(args)


epub = os.path.join(tmp, 'test.epub')
make_epub(epub)
fixtures = os.path.join(here, 'fixtures')
samples = [(os.path.join(fixtures, f), f) for f in ('sample-old.mobi', 'sample-joint.mobi', 'sample.azw3')]

py_azw3 = os.path.join(tmp, 'py.azw3')
kindlebook.write(epub, py_azw3, asin='CHECK-ASIN')
check_azw3(py_azw3, 'Windows/Linux AZW3')
for path, name in samples:
    out = os.path.join(tmp, name + '.py.epub')
    kindleread.to_epub(path, out)
    check_epub(out, 'Windows/Linux reads ' + name, nested='old' not in name)

if mac:
    mac_azw3 = os.path.join(tmp, 'mac.azw3')
    run_mac('--to-azw3', epub, mac_azw3, 'CHECK-ASIN')
    check_azw3(mac_azw3, 'Mac AZW3')
    for path, name in samples + [(py_azw3, 'Windows/Linux AZW3'), (mac_azw3, 'Mac AZW3')]:
        out = os.path.join(tmp, os.path.basename(path) + '.mac.epub')
        run_mac('--to-epub', path, out)
        check_epub(out, 'Mac reads ' + name, nested='old' not in name)
print('all Kindle checks passed')
