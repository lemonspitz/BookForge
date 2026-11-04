"""Translates EPUBs with Claude (a port of the Mac app's Translator.swift and ClaudeTranslator.swift).
First it reads samples from across the book and writes a style sheet (voices, register, fixed renderings
for names and terms); every batch of paragraphs is then translated with the language-pair guide plus that
style sheet. Apple Translate exists only on the Mac, so paragraphs Claude can't return are retried as
plain text and otherwise left as they were."""
import copy, json, os, re, shutil, ssl, tempfile, time, urllib.error, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor

from lxml import etree

from . import languages, styleguides
from .epubwriter import write_zip
from .util import Failure

MODEL = 'claude-opus-5-5'
# BOOKFORGE_API_URL points at a local mock server for testing
ENDPOINT = os.environ.get('BOOKFORGE_API_URL') or 'https://api.anthropic.com/v1/messages'
# per million tokens, claude-opus-5-5
INPUT_PRICE, OUTPUT_PRICE, CACHE_READ_PRICE = 4.0, 20.0, 0.20


BLOCK_TAGS = ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'dt', 'dd', 'figcaption', 'td', 'th', 'blockquote', 'title']
# inline elements that can be rebuilt from text with <i>/<b>/<br/>; anything else (links, images…) is kept in place
FLATTENABLE = {'i', 'em', 'cite', 'b', 'strong', 'br', 'span', 'small', 'u', 'ruby', 'rb', 'rt', 'rp', 'font'}


class Usage:
    def __init__(self):
        self.input = self.output = self.cache_read = self.cache_write = 0

    def add(self, u):
        self.input += u.input
        self.output += u.output
        self.cache_read += u.cache_read
        self.cache_write += u.cache_write

    @property
    def dollars(self):
        return (self.input * INPUT_PRICE + self.output * OUTPUT_PRICE + self.cache_read * CACHE_READ_PRICE
                + self.cache_write * INPUT_PRICE * 1.25) / 1_000_000


class Refused(Exception):
    pass


class Truncated(Exception):
    pass


class Malformed(Exception):
    pass


def estimate(source_chars, src, dst):
    """Rough cost before starting: source text in, translation (plus some reasoning) out."""
    src_l, dst_l = languages.get(src) or {}, languages.get(dst) or {}
    src_tokens = source_chars / src_l.get('charsPerToken', 3.6)
    # the same text takes more tokens in some languages
    ratio = dst_l.get('weight', 1.0) / src_l.get('weight', 1.0)
    expansion = 1.1 if ratio == 1 else max(0.9, ratio)
    chunks = source_chars / 3000 + 1
    inp = src_tokens * 1.25 + chunks * 600
    cached = chunks * 3000
    out = src_tokens * expansion * 1.35
    return (inp * INPUT_PRICE + cached * CACHE_READ_PRICE + out * OUTPUT_PRICE) / 1_000_000


def source_chars(epub):
    n = 0
    with zipfile.ZipFile(epub) as z:
        for name in z.namelist():
            if name.lower().endswith(('.xhtml', '.html', '.htm')):
                n += len(re.sub(r'<[^>]+>', '', z.read(name).decode('utf-8', 'replace')))
    return n


def ssl_context():
    ctx = ssl.create_default_context()
    if not ctx.cert_store_stats().get('x509_ca'):
        for f in ('/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt', '/etc/ssl/ca-bundle.pem',
                  '/etc/ssl/cert.pem'):
            if os.path.exists(f):
                ctx.load_verify_locations(f)
                break
    return ctx


# MARK: - Claude

class Claude:
    def __init__(self, api_key, src, dst):
        self.api_key, self.src, self.dst = api_key, src, dst
        self.ctx = ssl_context()

    def call(self, system, prompt, schema, max_tokens, usage):
        """POST /v1/messages with a cached system prompt and JSON-schema output; retries rate limits and overloads."""
        body = {
            'model': MODEL,
            'max_tokens': max_tokens,
            # re-run on Anthropic's recommended model if a safety classifier declines (e.g. violent scenes)
            'fallbacks': 'default',
            'output_config': {'effort': 'medium', 'format': {'type': 'json_schema', 'schema': schema}},
            'system': [{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}],
            'messages': [{'role': 'user', 'content': prompt}],
        }
        data = json.dumps(body).encode('utf-8')
        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(ENDPOINT, data=data, method='POST', headers={
                'content-type': 'application/json', 'x-api-key': self.api_key,
                'anthropic-version': '2026-10-21', 'anthropic-beta': 'server-side-fallback-2026-07-01'})
            try:
                with urllib.request.urlopen(req, timeout=600, context=self.ctx if ENDPOINT.startswith('https') else None) as r:
                    text = r.read().decode('utf-8', 'replace')
            except urllib.error.HTTPError as e:
                code, text = e.code, e.read().decode('utf-8', 'replace')
                if code in (401, 403):
                    raise Failure('The Claude API key was rejected. Check it in Book Forge.')
                if code in (408, 409, 429) or 500 <= code <= 599:
                    if attempt >= 6:
                        raise Failure('Claude API error %d: %s' % (code, text[:300]))
                    try:
                        wait = float(e.headers.get('retry-after'))
                    except (TypeError, ValueError):
                        wait = min(60, 2 ** attempt * 2)
                    time.sleep(wait)
                    continue
                raise Failure('Claude API error %d: %s' % (code, text[:300]))
            except (urllib.error.URLError, OSError) as e:
                if attempt < 5:
                    time.sleep(attempt * 3)
                    continue
                raise Failure("Couldn't reach Claude: %s" % e)
            try:
                obj = json.loads(text)
            except ValueError:
                raise Malformed(text[:200])
            u = obj.get('usage') or {}
            usage.input += u.get('input_tokens') or 0
            usage.output += u.get('output_tokens') or 0
            usage.cache_read += u.get('cache_read_input_tokens') or 0
            usage.cache_write += u.get('cache_creation_input_tokens') or 0
            stop = obj.get('stop_reason')
            if stop == 'refusal':
                raise Refused()
            if stop == 'max_tokens':
                raise Truncated()
            # the answer is the last text block (fallback turns may add other blocks before it)
            texts = [b.get('text') for b in obj.get('content') or [] if b.get('type') == 'text']
            try:
                return json.loads(texts[-1])
            except (IndexError, ValueError, TypeError):
                raise Malformed(text[:200])

    def system(self):
        return styleguides.base(self.src, self.dst) + '\n\n' + styleguides.pair(self.src, self.dst)

    def learn_style(self, samples, usage):
        """Asks Claude to study excerpts and return a style sheet for this book and language pair."""
        schema = {
            'type': 'object', 'additionalProperties': False,
            'required': ['narration', 'dialogue', 'characters', 'glossary', 'notes'],
            'properties': {
                'narration': {'type': 'string', 'description': 'Narrative voice: person, tense, tone, sentence rhythm, and how to reproduce it in the target language.'},
                'dialogue': {'type': 'string', 'description': 'Overall dialogue register (slang, particles, politeness) and how to render it.'},
                'characters': {'type': 'array', 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['name', 'voice', 'rendering'],
                    'properties': {
                        'name': {'type': 'string'},
                        'voice': {'type': 'string', 'description': 'How this character speaks in the source.'},
                        'rendering': {'type': 'string', 'description': 'How to voice them in the target language (pronouns, endings, register).'},
                    }}},
                'glossary': {'type': 'array', 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['source', 'target'],
                    'properties': {'source': {'type': 'string'}, 'target': {'type': 'string'}}}},
                'notes': {'type': 'string', 'description': 'Anything else a translator must keep consistent.'},
            },
        }
        src, dst = languages.prose(self.src), languages.prose(self.dst)
        names = (languages.get(self.dst) or {}).get('names', '')
        prompt = ('Below are excerpts from across a %s novel that is about to be translated into %s. '
                  'Before translating, study them and write a translation style sheet: the narrative voice; the dialogue register; '
                  "every recurring character's way of speaking and how to voice them in %s; and a glossary of names, titles, places "
                  'and recurring terms with the exact %s rendering to use every time%s. Be specific and concise.\n\n'
                  % (src, dst, dst, dst, ' (%s)' % names if names else '')
                  + '\n\n'.join('<excerpt %d>\n%s\n</excerpt>' % (i + 1, s) for i, s in enumerate(samples)))
        obj = self.call(self.system(), prompt, schema, 16000, usage)
        if not isinstance(obj, dict):
            raise Failure('Unexpected reply from Claude (style sheet).')
        sheet = 'Style sheet learned from this book:\n'
        sheet += 'Narration: %s\n' % obj.get('narration', '')
        sheet += 'Dialogue: %s\n' % obj.get('dialogue', '')
        for c in obj.get('characters') or []:
            sheet += 'Character %s: %s → %s\n' % (c.get('name', ''), c.get('voice', ''), c.get('rendering', ''))
        glossary = ['%s = %s' % (g.get('source', ''), g.get('target', '')) for g in obj.get('glossary') or []]
        if glossary:
            sheet += 'Glossary: ' + '; '.join(glossary) + '\n'
        sheet += 'Notes: %s' % obj.get('notes', '')
        return sheet

    def translate(self, paragraphs, style_sheet, progress):
        """Translates `paragraphs` (inline <i>/<b>/<br/> allowed) in parallel batches. Paragraphs Claude
        can't return cleanly come back as None. Returns (results, usage)."""
        system = self.system() + '\n\n' + style_sheet
        # batches of ~3000 characters, never splitting a paragraph
        batches, start, size = [], 0, 0
        for i, p in enumerate(paragraphs):
            if size > 0 and (size + len(p) > 3000 or i - start >= 40):
                batches.append((start, i))
                start, size = i, 0
            size += len(p)
        if start < len(paragraphs):
            batches.append((start, len(paragraphs)))
        out = [None] * len(paragraphs)
        usage = Usage()
        done = 0

        def one(rng):
            a, b = rng
            # the source text just before this batch, so each batch can run independently
            context = '\n'.join(paragraphs[max(0, a - 4):a])[-800:]
            u = Usage()
            return rng, self.batch(paragraphs[a:b], context, system, u), u
        with ThreadPoolExecutor(4) as pool:
            for (a, b), result, u in pool.map(one, batches):
                if result:
                    out[a:b] = result
                usage.add(u)
                done += b - a
                progress(done / max(1, len(paragraphs)), usage)
        return out, usage

    def batch(self, paras, context, system, usage, depth=0):
        """One batch; on a count mismatch, refusal or cut-off it retries the two halves separately."""
        schema = {
            'type': 'object', 'additionalProperties': False, 'required': ['paragraphs'],
            'properties': {'paragraphs': {'type': 'array', 'items': {'type': 'string'},
                                          'description': 'One translated paragraph per numbered input paragraph, same order, without the numbers.'}},
        }
        numbered = '\n\n'.join('[%d] %s' % (i + 1, p) for i, p in enumerate(paras))
        prompt = ('<context>\n%s\n</context>\n\n' % context if context else '') + 'Translate these %d paragraphs:\n\n%s' % (len(paras), numbered)
        try:
            obj = self.call(system, prompt, schema, 16000, usage)
            lst = obj.get('paragraphs') if isinstance(obj, dict) else None
            if isinstance(lst, list) and len(lst) == len(paras) and all(isinstance(x, str) for x in lst):
                return lst
        except (Refused, Truncated, Malformed):
            pass
        if len(paras) <= 1 or depth >= 4:
            return None
        mid = len(paras) // 2
        a = self.batch(paras[:mid], context, system, usage, depth + 1)
        b = self.batch(paras[mid:], '\n'.join(paras[:mid][-2:]), system, usage, depth + 1)
        if a and b:
            return a + b
        return None


# MARK: - EPUB

XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'


def relabel(doc, src, dst):
    """Marks the pages as the new language: e-readers choose fonts by it (Chinese, Japanese and Thai above all)."""
    root = doc.getroot()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for a in (XML_LANG, 'lang'):
            v = el.get(a)
            if (el is root and (v is not None or a == XML_LANG)) or (v is not None and languages.normalize(v) == src):
                el.set(a, dst)


def local(el):
    return etree.QName(el).localname.lower() if isinstance(el.tag, str) else ''


def ns_of(el):
    return etree.QName(el).namespace


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def plain_text(tagged):
    t = tagged.replace('<br/>', ' ')
    t = re.sub(r'<[^>]+>', '', t)
    t = t.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    return re.sub(r'\s+', ' ', t).strip()


def inline(el):
    """Element content as text with only <i>, <b> and <br/> kept (em/strong folded into i/b)."""
    out = esc(el.text or '')
    for c in el:
        if isinstance(c.tag, str):
            inner = inline(c)
            n = local(c)
            if n in ('i', 'em', 'cite'):
                out += '<i>%s</i>' % inner if inner else ''
            elif n in ('b', 'strong'):
                out += '<b>%s</b>' % inner if inner else ''
            elif n == 'br':
                out += '<br/>'
            elif n in ('rt', 'rp'):
                pass            # furigana readings
            else:
                out += inner
        out += esc(c.tail or '')
    return out


def tagged(el, shells):
    """Like `inline`, but other elements become numbered placeholders whose shells are collected."""
    out = esc(el.text or '')
    for c in el:
        if not isinstance(c.tag, str):
            out += esc(c.tail or '')
            continue
        n = local(c)
        if n in FLATTENABLE:
            inner = tagged(c, shells)
            if n in ('i', 'em', 'cite'):
                out += '<i>%s</i>' % inner if inner else ''
            elif n in ('b', 'strong'):
                out += '<b>%s</b>' % inner if inner else ''
            elif n == 'br':
                out += '<br/>'
            elif n not in ('rt', 'rp'):
                out += inner
        else:
            shell = copy.deepcopy(c)
            shell.tail = None
            shells.append(shell)
            k = len(shells)
            inner = tagged(c, shells)
            if not plain_text(inner):
                out += '<x%d/>' % k     # image or empty anchor: keep the original as is
            else:
                for g in list(shell):
                    shell.remove(g)
                shell.text = None
                out += '<x%d>%s</x%d>' % (k, inner, k)
        out += esc(c.tail or '')
    return out


def parse_fragment(text, ns):
    return etree.fromstring('<x%s>%s</x>' % (' xmlns="%s"' % ns if ns else '', text))


def set_inline(el, text):
    """Replaces an element's content with translated text, rebuilding <i>/<b>/<br/> when present."""
    for c in list(el):
        el.remove(c)
    el.text = None
    if '<' in text:
        try:
            w = parse_fragment(text, ns_of(el))
            el.text = w.text
            for c in list(w):
                el.append(c)
            return
        except etree.XMLSyntaxError:
            text = plain_text(text)
    el.text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')


def rebuild(el, text, shells):
    """Rebuilds a placeholder unit; returns False if Claude didn't keep every placeholder."""
    ns = ns_of(el)
    try:
        w = parse_fragment(text, ns)
    except etree.XMLSyntaxError:
        return False
    used = set()

    def convert(node):
        name = local(node)
        m = re.match(r'^x(\d+)$', name)
        if m and 1 <= int(m.group(1)) <= len(shells):
            k = int(m.group(1))
            used.add(k)
            new = copy.deepcopy(shells[k - 1])
            if node.text or len(node):
                for g in list(new):
                    new.remove(g)
                new.text = node.text
                for c in node:
                    new.append(convert(c))
        else:
            new = etree.Element('{%s}%s' % (ns, name) if ns else name)
            new.text = node.text
            for c in node:
                new.append(convert(c))
        new.tail = node.tail
        return new
    kids = [convert(c) for c in w]
    if len(used) != len(shells):
        return False
    for c in list(el):
        el.remove(c)
    el.text = w.text
    for k in kids:
        el.append(k)
    return True


def text_runs(el):
    """Each non-empty text run inside an element, as (element, 'text' | 'tail')."""
    runs = []
    if (el.text or '').strip():
        runs.append((el, 'text'))
    for d in el.iterdescendants():
        if not isinstance(d.tag, str):
            continue
        if local(d) not in ('rt', 'rp') and (d.text or '').strip():
            runs.append((d, 'text'))
        if (d.tail or '').strip():
            runs.append((d, 'tail'))
    return runs


def set_run(run, text):
    el, attr = run
    s = getattr(el, attr) or ''
    lead = s[:len(s) - len(s.lstrip())]
    trail = s[len(s.rstrip()):]
    setattr(el, attr, lead + text + trail)


def samples(paras):
    """About a dozen excerpts spread across the book, favouring passages with dialogue."""
    body = [p for p in paras if len(p) > 40]
    if not body:
        return []
    windows, per, out = 12, 1400, []
    for w in range(windows):
        center = len(body) * (2 * w + 1) // (2 * windows)
        # slide a little to land on dialogue if there is some nearby
        start = next((i for i in range(max(0, center - 6), min(len(body), center + 6))
                      if any(q in body[i] for q in ('"', '“', '「'))), center)
        text, i = '', start
        while i < len(body) and len(text) < per:
            text += body[i] + '\n'
            i += 1
        out.append(text)
    return out


def translate_epub(epub, src, dst, output, api_key, status):
    """Returns dict(paragraphs, left, cost, style_sheet). `status(line, fraction)`."""
    if src == dst:
        raise Failure('The book is already in %s.' % languages.name(dst))
    if not api_key:
        raise Failure('Add your Claude API key first.')
    tmp = tempfile.mkdtemp(prefix='bookforge-tr-')
    try:
        with zipfile.ZipFile(epub) as z:
            z.extractall(tmp)
        opf_rel = None
        try:
            c = etree.parse(os.path.join(tmp, 'META-INF', 'container.xml'))
            opf_rel = c.xpath("//*[local-name()='rootfile']/@full-path")[0]
        except Exception:
            pass
        opf_file = os.path.join(tmp, opf_rel) if opf_rel else next(
            (os.path.join(r, f) for r, _, fs in os.walk(tmp) for f in fs if f.lower().endswith('.opf')), None)
        if not opf_file or not os.path.exists(opf_file):
            raise Failure('This EPUB has no package file (OPF).')
        parser = etree.XMLParser(recover=True, resolve_entities=False, huge_tree=True)
        opf = etree.parse(opf_file, parser)
        base = os.path.dirname(opf_file)
        from urllib.parse import unquote
        href_by_id = {i.get('id'): unquote(i.get('href') or '') for i in opf.xpath("//*[local-name()='item']")}
        ordered = []
        for ref in opf.xpath("//*[local-name()='itemref']"):
            h = href_by_id.get(ref.get('idref'))
            if h:
                ordered.append(os.path.normpath(os.path.join(base, h)))
        all_files = [os.path.normpath(os.path.join(r, f)) for r, _, fs in os.walk(tmp) for f in fs]
        for f in all_files:
            if f.lower().endswith(('.xhtml', '.html', '.htm')) and f not in ordered:
                ordered.append(f)

        any_block = ' or '.join("local-name()='%s'" % t for t in BLOCK_TAGS)
        docs, units = [], []      # unit: dict(el, text, plain, shells)
        for f in ordered:
            if not os.path.exists(f):
                continue
            try:
                doc = etree.parse(f, parser)
            except Exception:
                continue
            docs.append((f, doc))
            for el in doc.xpath('//*[%s]' % any_block):
                # innermost text blocks only, e.g. a <p> inside an <li> is translated, not the <li>
                if el.xpath('.//*[%s]' % any_block):
                    continue
                if all(local(d) in FLATTENABLE for d in el.iterdescendants() if isinstance(d.tag, str)):
                    text = inline(el).strip()
                    plain = plain_text(text)
                    if plain:
                        units.append(dict(el=el, text=text, plain=plain, shells=None))
                else:
                    # links, images, footnote markers become placeholders <x1>…</x1> / <x2/> so the
                    # sentence is translated whole and the elements are put back afterwards
                    shells = []
                    text = tagged(el, shells).strip()
                    if plain_text(text):
                        units.append(dict(el=el, text=text, plain=plain_text(text), shells=shells))
        # table of contents labels for older readers (toc.ncx)
        ncx = None
        ncx_file = next((f for f in all_files if f.lower().endswith('.ncx')), None)
        if ncx_file:
            try:
                ncx = (ncx_file, etree.parse(ncx_file, parser))
                for el in ncx[1].xpath("//*[local-name()='text']"):
                    if (el.text or '').strip():
                        units.append(dict(el=el, text=esc(el.text), plain=el.text, shells=None))
            except Exception:
                ncx = None

        claude = Claude(api_key, src, dst)
        usage = Usage()
        status('Reading the book to learn its style…', 0)
        sheet = claude.learn_style(samples([u['plain'] for u in units]), usage)
        learned = usage.dollars

        def prog(p, u):
            status('Translating with Claude… %d%% ($%.2f so far)' % (int(p * 100), learned + u.dollars), p)
        results, used = claude.translate([u['text'] for u in units], sheet, prog)
        usage.add(used)

        # placeholders Claude lost, or paragraphs it couldn't return: one more try as plain text runs
        status('Writing the translated EPUB…', 1)
        retry_runs, retry_units = [], []
        for i, u in enumerate(units):
            r = results[i]
            if u['shells'] is not None:
                if r is not None and rebuild(u['el'], r, u['shells']):
                    continue
                retry_runs += text_runs(u['el'])
            elif r is not None:
                set_inline(u['el'], r)
            else:
                retry_units.append(u)
        left = 0
        if retry_runs or retry_units:
            status('Retrying %d passages…' % (len(retry_runs) + len(retry_units)), 1)
            texts = [esc((getattr(el, a) or '').strip()) for el, a in retry_runs] + [esc(u['plain']) for u in retry_units]
            again, u2 = claude.translate(texts, sheet, lambda p, u: None)
            usage.add(u2)
            for k, run in enumerate(retry_runs):
                if again[k] is not None:
                    set_run(run, plain_text(again[k]))
                else:
                    left += 1
            for k, u in enumerate(retry_units):
                r = again[len(retry_runs) + k]
                if r is not None:
                    set_inline(u['el'], r)
                else:
                    left += 1

        for f, doc in docs:
            relabel(doc, src, dst)
            doc.write(f, xml_declaration=True, encoding='utf-8')
        if ncx:
            relabel(ncx[1], src, dst)
            ncx[1].write(ncx[0], xml_declaration=True, encoding='utf-8')
        for n in opf.xpath("//*[local-name()='language']"):
            n.text = dst
        t = opf.xpath("//*[local-name()='metadata']/*[local-name()='title']")
        if t:
            t[0].text = '%s (%s)' % (t[0].text or '', (languages.get(dst) or {}).get('native', dst))
        opf.write(opf_file, xml_declaration=True, encoding='utf-8')

        entries = []
        for f in sorted(all_files):
            rel = os.path.relpath(f, tmp).replace(os.sep, '/')
            if rel == 'mimetype':
                continue
            with open(f, 'rb') as fh:
                entries.append((rel, fh.read()))
        write_zip(output, entries)
        return dict(paragraphs=len(units), left=left, cost=usage.dollars, style_sheet=sheet)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
