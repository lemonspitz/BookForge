"""Lines of text from each PDF page: the PDF's own text layer (Calibre's pdftohtml), or Tesseract OCR
for scanned pages. Coordinates are normalised 0…1 with y growing downwards."""
import io, os, re, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor

from . import languages
from .library import PDFIUM, pdf_info, render_page
from .util import Failure, no_window, tesseract


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




def page_count(pdf):
    return int(pdf_info(pdf).get('Pages') or 0)


def text_layers(pdf):
    """{page index: [Line]} for pages whose text layer has real text (pdfium's text segments, one per run of a line)."""
    with PDFIUM:
        return _text_layers(pdf)


def _text_layers(pdf):
    """Builds lines from single characters: pdfium's text runs break at accents and Thai vowel marks."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    out = {}
    doc = pdfium.PdfDocument(pdf)
    try:
        for i in range(len(doc)):
            page = doc[i]
            W, H = page.get_size()
            tp = page.get_textpage()
            lines, units, box = [], [], None

            def flush():
                # UTF-16 units (characters beyond the BMP arrive as surrogate pairs)
                text = ''.join(units).encode('utf-16-le', 'surrogatepass').decode('utf-16-le', 'replace')
                text = ' '.join(text.split())
                if text and box:
                    l, b, r, t = box
                    lines.append(Line(text, l / W, r / W, (H - t) / H, (H - b) / H))

            for k in range(tp.count_chars()):
                u = raw.FPDFText_GetUnicode(tp.raw, k)
                if u in (10, 13):                   # pdfium's line breaks
                    if units:
                        flush()
                    units, box = [], None
                    continue
                if u == 0 or u == 0xFFFE:
                    continue
                units.append(chr(u))
                if raw.FPDFText_IsGenerated(tp.raw, k) or chr(u).isspace():
                    continue
                l, b, r, t = tp.get_charbox(k)
                if r > l and t > b:
                    box = (l, b, r, t) if box is None else (min(box[0], l), min(box[1], b), max(box[2], r), max(box[3], t))
            if units:
                flush()
            tp.close()
            page.close()
            if sum(len(x.text) for x in lines) > 40:
                out[i] = lines
    finally:
        doc.close()
    return out


_langs_cache = None


def ocr_languages(language):
    """Tesseract language string for the book's language, using what's installed."""
    global _langs_cache
    exe = tesseract()
    if not exe:
        raise Failure('This PDF is scanned, so it has to be read with text recognition. Install Tesseract OCR '
                      '(Windows: github.com/UB-Mannheim/tesseract/wiki, choosing the languages your books are in; '
                      'Linux: the tesseract-ocr package and one per language, such as tesseract-ocr-ind, -jpn, -kor, '
                      '-chi-sim, -chi-tra, -tha or -vie), then try again.')
    if _langs_cache is None:
        try:
            r = subprocess.run([exe, '--list-langs'], capture_output=True, text=True, **no_window())
            _langs_cache = set(l.strip() for l in (r.stdout + r.stderr).splitlines()[1:] if l.strip())
        except Exception:
            _langs_cache = set()
    want = (languages.get(language) or {}).get('tesseract') or ['eng']
    have = [l for l in want if not _langs_cache or l in _langs_cache]
    if not have:
        raise Failure("Tesseract doesn't have the %s language installed. Add it (the “%s” language data) and try again."
                      % (languages.name(language) if languages.get(language) else 'English', want[0]))
    return '+'.join(have)


def is_cjk(c):
    v = ord(c)
    return 0x3000 <= v <= 0x30FF or 0x3400 <= v <= 0x4DBF or 0x4E00 <= v <= 0x9FFF or 0xFF00 <= v <= 0xFFEF


def joins_without_space(c):
    """Thai, like Chinese and Japanese, puts no spaces between words."""
    return is_cjk(c) or 0x0E00 <= ord(c) <= 0x0E7F


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
            if text and not (joins_without_space(text[-1]) and joins_without_space(word[0])):
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
        jpeg = render_page(pdf, index, 300, gray=True, crop=region)
        text = ''.join(l.text for l in ocr_image(jpeg, ocr_languages(language)))
    except Exception:
        return None
    c = next((ch for ch in text if ch.isalpha()), None)
    return c if c and c.isupper() else None
