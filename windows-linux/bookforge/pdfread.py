"""Lines of text from each PDF page: the PDF's own text layer (Calibre's pdftohtml), or Tesseract OCR
for scanned pages. Coordinates are normalised 0…1 with y growing downwards."""
import io, os, re, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor

from .library import pdf_info, render_page
from .util import Failure, no_window, pdf_tools, tesseract


class Line:
    __slots__ = ('text', 'x0', 'x1', 'y0', 'y1')

    def __init__(self, text, x0, x1, y0, y1):
        self.text, self.x0, self.x1, self.y0, self.y1 = text, x0, x1, y0, y1

    def copy(self):
        return Line(self.text, self.x0, self.x1, self.y0, self.y1)

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0

    @property
    def mid(self):
        return (self.x0 + self.x1) / 2

    def __repr__(self):
        return 'Line(%r, %.3f-%.3f, %.3f-%.3f)' % (self.text, self.x0, self.x1, self.y0, self.y1)


class PageText:
    def __init__(self, index, lines):
        self.index = index
        self.lines = lines


TESS_LANGS = {'id': ['ind', 'eng'], 'en': ['eng'], 'ja': ['jpn', 'eng']}


def page_count(pdf):
    try:
        return int(pdf_info(pdf).get('Pages', '0'))
    except ValueError:
        return 0


def text_layers(pdf):
    """{page index: [Line]} for pages whose text layer has real text."""
    from lxml import etree
    _, _, pdftohtml = pdf_tools()
    out = {}
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, 'out')
        subprocess.run([pdftohtml, '-xml', '-i', '-q', '-hidden', '-nodrm', '-enc', 'UTF-8', '-nomerge', pdf, base],
                       capture_output=True, **no_window())
        xml = base + '.xml'
        if not os.path.exists(xml):
            return out
        root = etree.parse(xml, etree.XMLParser(recover=True, huge_tree=True)).getroot()
    for page in root.iter('page'):
        i = int(page.get('number', '1')) - 1
        W, H = float(page.get('width') or 1), float(page.get('height') or 1)
        lines = []
        for t in page.iter('text'):
            s = ''.join(t.itertext()).strip()
            if not s:
                continue
            x, y = float(t.get('left', 0)), float(t.get('top', 0))
            w, h = float(t.get('width', 0)), float(t.get('height', 0))
            lines.append(Line(s, x / W, (x + w) / W, y / H, (y + h) / H))
        if sum(len(l.text) for l in lines) > 40:
            out[i] = lines
    return out


_langs_cache = None


def ocr_languages(language):
    """Tesseract language string for the book's language, using what's installed."""
    global _langs_cache
    exe = tesseract()
    if not exe:
        raise Failure('This PDF is scanned, so it has to be read with text recognition. Install Tesseract OCR '
                      '(Windows: github.com/UB-Mannheim/tesseract/wiki, choosing the Indonesian and Japanese languages; '
                      'Linux: the tesseract-ocr, tesseract-ocr-ind and tesseract-ocr-jpn packages), then try again.')
    if _langs_cache is None:
        try:
            r = subprocess.run([exe, '--list-langs'], capture_output=True, text=True, **no_window())
            _langs_cache = set(l.strip() for l in (r.stdout + r.stderr).splitlines()[1:] if l.strip())
        except Exception:
            _langs_cache = set()
    want = TESS_LANGS.get(language, ['eng'])
    have = [l for l in want if not _langs_cache or l in _langs_cache]
    if not have:
        raise Failure("Tesseract doesn't have the %s language installed. Add it (the “%s” language data) and try again."
                      % ({'id': 'Indonesian', 'ja': 'Japanese'}.get(language, 'English'), want[0]))
    return '+'.join(have)


def is_cjk(c):
    v = ord(c)
    return 0x3000 <= v <= 0x30FF or 0x4E00 <= v <= 0x9FFF or 0xFF00 <= v <= 0xFFEF


def ocr_image(jpeg, langs):
    """Lines Tesseract finds in an image (bytes)."""
    exe = tesseract()
    with tempfile.TemporaryDirectory() as d:
        img = os.path.join(d, 'page.jpg')
        with open(img, 'wb') as f:
            f.write(jpeg)
        r = subprocess.run([exe, img, 'stdout', '-l', langs, 'tsv'], capture_output=True, **no_window())
    rows = r.stdout.decode('utf-8', 'replace').splitlines()
    W = H = None
    groups = {}
    order = []
    for row in rows[1:]:
        c = row.split('\t')
        if len(c) < 12:
            continue
        level = c[0]
        left, top, w, h = (int(float(v)) for v in c[6:10])
        if level == '1':
            W, H = max(1, w), max(1, h)
            continue
        if level != '5':
            continue
        text = c[11].strip()
        try:
            conf = float(c[10])
        except ValueError:
            conf = 0
        if not text or conf < 0:
            continue
        k = (c[2], c[3], c[4])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append((text, left, top, left + w, top + h))
    if not W:
        from PIL import Image
        W, H = Image.open(io.BytesIO(jpeg)).size
    lines = []
    for k in order:
        ws = groups[k]
        text = ''
        for word, *_ in ws:
            if text and not (is_cjk(text[-1]) and is_cjk(word[0])):
                text += ' '
            text += word
        x0 = min(w[1] for w in ws); y0 = min(w[2] for w in ws)
        x1 = max(w[3] for w in ws); y1 = max(w[4] for w in ws)
        lines.append(Line(text, x0 / W, x1 / W, y0 / H, y1 / H))
    return lines


def read(pdf, language, force_ocr, progress):
    """Reads every page, OCRing pages without a text layer. `progress(done, total)`."""
    n = page_count(pdf)
    if n == 0:
        raise Failure("Couldn't open the PDF.")
    layers = {} if force_ocr else text_layers(pdf)
    todo = [i for i in range(n) if i not in layers]
    results = {i: PageText(i, layers[i]) for i in layers}
    if todo and not tesseract() and len(layers) >= n / 2:
        # a text PDF whose other pages are pictures or blank: no recognition needed
        for i in todo:
            results[i] = PageText(i, [])
        todo = []
    done = len(results)
    progress(done, n)
    if todo:
        langs = ocr_languages(language)

        def one(i):
            img = render_page(pdf, i, 200, gray=True)
            return PageText(i, ocr_image(img, langs) if img else [])
        workers = max(2, (os.cpu_count() or 4) - 2)
        with ThreadPoolExecutor(workers) as pool:
            for p in pool.map(one, todo):
                results[p.index] = p
                done += 1
                progress(done, n)
    return [results[i] for i in range(n)]


def read_initial(pdf, index, region, language):
    """Tries to read a drop-cap initial from a region (x, y, w, h normalised, top-down) of a page."""
    if not tesseract():
        return None
    try:
        from PIL import Image
        jpeg = render_page(pdf, index, 300, gray=True)
        img = Image.open(io.BytesIO(jpeg))
        W, H = img.size
        x, y, w, h = region
        crop = img.crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H)))
        buf = io.BytesIO()
        crop.save(buf, 'JPEG')
        text = ''.join(l.text for l in ocr_image(buf.getvalue(), ocr_languages(language)))
    except Exception:
        return None
    c = next((ch for ch in text if ch.isalpha()), None)
    return c if c and c.isupper() else None
