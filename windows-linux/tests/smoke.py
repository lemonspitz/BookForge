"""Checks a build without an e-reader or a screen: makes a sample library (EPUB from a generated PDF), opens the
main window, the device and convert screens and the reader off screen, and saves screenshots to the folder given."""
import os, sys, tempfile

out = os.path.abspath(sys.argv[1])
os.makedirs(out, exist_ok=True)
lib_dir = tempfile.mkdtemp(prefix='bf-smoke-')
os.environ['BOOKFORGE_LIBRARY'] = lib_dir
if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')     # Windows' off-screen mode has no fonts
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

# a two-chapter sample PDF, drawn with Pillow (no fonts needed)
from PIL import Image, ImageDraw
pages = []
for p in range(4):
    img = Image.new('RGB', (620, 880), 'white')
    d = ImageDraw.Draw(img)
    if p in (1, 3):
        d.text((260, 80), 'Chapter %s' % ('One' if p == 1 else 'Two'), fill='black')
    for i in range(30):
        d.text((60 + (20 if i % 6 == 0 else 0), 140 + i * 22), 'sample words for the smoke test of book forge ' * 2, fill='black')
    pages.append(img)
pdf = os.path.join(lib_dir, 'Smoke Test - Book Forge.pdf')
pages[0].save(pdf, save_all=True, append_images=pages[1:], resolution=72)

from bookforge.qt import QApplication, QTimer, Qt, QWebEngineView  # noqa: F401
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(['bookforge-smoke'])
from bookforge import theme
theme.apply(app)
from bookforge import devices, languages, library, pdfread, styleguides
from bookforge.ui_main import MainWindow
L = library.shared()
L.rescan()
print('library books:', len(L.books))
print('pdf pages:', pdfread.page_count(pdf))
print('languages:', ', '.join(languages.CODES))
assert {'id', 'en', 'ja', 'ko', 'zh-Hans', 'zh-Hant', 'th', 'vi'} <= set(languages.CODES)
assert all(styleguides.pair(a, b) for a in languages.CODES for b in languages.CODES if a != b)
w = MainWindow()
w.resize(1100, 720)
w.show()
devices.shared()._rescan_blocking()
print('reader state:', devices.shared().state, devices.shared().state_name)


def shot(name):
    w.grab().save(os.path.join(out, name + '.png'))


steps = [lambda: shot('library'), lambda: w.side.setCurrentItem(w.dev_item), lambda: shot('device'),
         lambda: w.forge.load(pdf), lambda: w.side.setCurrentItem(w.forge_item), lambda: shot('convert'),
         lambda: print('translate to:', [w.forge.to_c.itemText(i) for i in range(w.forge.to_c.count())]),
         lambda: app.quit()]


def run(i=0):
    steps[i]()
    if i + 1 < len(steps):
        QTimer.singleShot(1500, lambda: run(i + 1))


QTimer.singleShot(1500, run)
app.exec()
print('screenshots:', sorted(os.listdir(out)))

# Kindle books without Calibre: EPUB -> AZW3 -> EPUB keeps the text, contents and cover
import io, zipfile
from bookforge import kindlebook, kindleread
epub = os.path.join(lib_dir, 'kindle-test.epub')
with zipfile.ZipFile(epub, 'w') as z:
    z.writestr('mimetype', 'application/epub+zip')
    z.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
               '<rootfile full-path="c.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    z.writestr('c.opf', '<package xmlns="http://www.idpf.org/2007/opf" version="2.0"><metadata xmlns:dc="http://purl.org/'
               'dc/elements/1.1/"><dc:title>Kindle Test</dc:title><dc:creator>Smoke</dc:creator><dc:language>ja</dc:language>'
               '</metadata><manifest><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/><item id="b" '
               'href="b.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/>'
               '</spine></package>')
    for n, body in (('a', '<h1>One</h1><p>吾輩は猫である。<a href="b.xhtml#x">next</a></p>'), ('b', '<h1 id="x">Two</h1><p>名前はまだ無い。</p>')):
        z.writestr(n + '.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>%s</title></head><body>%s</body></html>' % (n, body))
cover = io.BytesIO()
Image.new('RGB', (300, 450), 'navy').save(cover, 'JPEG')
azw3 = os.path.join(lib_dir, 'kindle-test.azw3')
kindlebook.write(epub, azw3, asin='SMOKE-1', cover=cover.getvalue())
from bookforge.readers import meta
m = meta.mobi(open(azw3, 'rb').read(65536))
assert (m.title, m.authors, m.asin, m.cdetype) == ('Kindle Test', ['Smoke'], 'SMOKE-1', 'EBOK'), vars(m)
back = os.path.join(lib_dir, 'kindle-test-back.epub')
kindleread.to_epub(azw3, back)
z = zipfile.ZipFile(back)
text = ''.join(z.read(n).decode('utf-8') for n in z.namelist() if n.startswith('OEBPS/text/'))
assert '吾輩は猫である' in text and '名前はまだ無い' in text and 'part0002.xhtml#x' in text, text[:500]
assert 'properties="cover-image"' in z.read('OEBPS/content.opf').decode()
print('kindle round trip: ok (%d bytes)' % os.path.getsize(azw3))
