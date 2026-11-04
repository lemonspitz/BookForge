"""Checks a build without an e-reader or a screen: makes a sample library (EPUB from a generated PDF), opens the
main window, the device and convert screens and the reader off screen, and saves screenshots to the folder given."""
import os, sys, tempfile

out = os.path.abspath(sys.argv[1])
os.makedirs(out, exist_ok=True)
lib_dir = tempfile.mkdtemp(prefix='bf-smoke-')
os.environ['BOOKFORGE_LIBRARY'] = lib_dir
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
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
from bookforge import devices, library, pdfread
from bookforge.ui_main import MainWindow
L = library.shared()
L.rescan()
print('library books:', len(L.books))
print('pdf pages:', pdfread.page_count(pdf))
w = MainWindow()
w.resize(1100, 720)
w.show()
devices.shared()._rescan_blocking()
print('reader state:', devices.shared().state, devices.shared().state_name)


def shot(name):
    w.grab().save(os.path.join(out, name + '.png'))


steps = [lambda: shot('library'), lambda: w.side.setCurrentItem(w.dev_item), lambda: shot('device'),
         lambda: w.forge.load(pdf), lambda: w.side.setCurrentItem(w.forge_item), lambda: shot('convert'),
         lambda: app.quit()]


def run(i=0):
    steps[i]()
    if i + 1 < len(steps):
        QTimer.singleShot(1500, lambda: run(i + 1))


QTimer.singleShot(1500, run)
app.exec()
print('screenshots:', sorted(os.listdir(out)))
