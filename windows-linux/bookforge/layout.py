"""Turns positioned lines into paragraphs, headings and scene breaks (a port of the Mac app's Layout.swift).
Pure heuristics: indents start paragraphs, big gaps are scene breaks, a short unpunctuated line at the
top of a page followed by space is a heading.

Blocks are tuples: ('heading', text), ('para', text, first, spaced), ('centered', [lines]), ('scene',), ('image', page)."""
import unicodedata

import regex as re

from .pdfread import joins_without_space, read_initial

ORNAMENTS = {"COS", "CoS", "cos", "cg", "Cg", "C3", "c3", "ca", "CG", "CS", "cs",
             "*", "* * *", "***", "~", "#", "☙", "❧", "§", "o0o", "oOo", "-o-"}
HAN_DASH = re.compile(r'(?<=[\u3400-\u4DBF\u4E00-\u9FFF])[—―－ー](?=[\u3400-\u4DBF\u4E00-\u9FFF])')
HOMOGLYPHS = str.maketrans({"а": "a", "р": "p", "К": "K", "е": "e", "п": "n", "ı": "i",
                            "‚": ",", "¿": "?", "ạ": "a", "ị": "i", "о": "o", "с": "c"})


def percentile(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * p))]


def median(xs):
    return percentile(xs, 0.5)


def clean_line(l):
    l = l.copy()
    l.text = l.text.strip().translate(HOMOGLYPHS)
    # some PDFs map Chinese characters to look-alike Kangxi radicals (⼀ for 一): put the real ones back
    if any(0x2E80 <= ord(c) <= 0x2FDF for c in l.text):
        l.text = ''.join(unicodedata.normalize('NFKC', c) if 0x2E80 <= ord(c) <= 0x2FDF else c for c in l.text)
    # text recognition reads 一 as a dash between two Chinese characters (第—章)
    l.text = HAN_DASH.sub('一', l.text)
    return l


def is_heading_text(t):
    s = t.strip()
    if not 1 <= len(s) <= 60:
        return False
    if s in ORNAMENTS:
        return False
    if s[-1] in '.,;!?"”’…-。，、！？：；」』' and not s.endswith('?!'):
        return False
    # Thai, Chinese, Japanese and Korean have no capitals
    return s[0].isupper() or s[0].isdigit() or (s[0].isalpha() and s[0].lower() == s[0].upper())


def pretty_heading(t):
    letters = ''.join(c for c in t if c.isalpha())
    if letters and letters == letters.upper():
        return ' '.join(w[:1].upper() + w[1:].lower() for w in t.split(' '))
    return t


def strip_running_heads(pages):
    """Removes page numbers and running heads/feet repeated across pages."""
    def k(s):
        return ''.join(c for c in s.lower() if c.isalpha())
    counts = {}
    for p in pages:
        for l in p.lines:
            if l.y1 < 0.07 or l.y0 > 0.93:
                counts[k(l.text)] = counts.get(k(l.text), 0) + 1
    limit = max(3, len(pages) // 10)
    for p in pages:
        keep = []
        for l in p.lines:
            if l.y1 < 0.07 or l.y0 > 0.93:
                s = l.text.strip()
                if re.match(r'^[\divxlcIVXLC\-–— ]{1,8}$', s):
                    continue
                if counts.get(k(s), 0) >= limit and k(s):
                    continue
            keep.append(l)
        p.lines = keep


def merge_rows(lines):
    """OCR may split a justified line into fragments; rejoin pieces on the same row."""
    groups = []
    for l in sorted(lines, key=lambda l: l.y0):
        if groups and abs(l.y0 - groups[-1][0].y0) < 0.4 * max(0.012, groups[-1][0].height):
            groups[-1].append(l)
        else:
            groups.append([l])
    out = []
    for g in groups:
        s = sorted(g, key=lambda l: l.x0)
        r = s[0].copy()
        for l in s[1:]:
            r.text += ' ' + l.text
            r.x1, r.y0, r.y1 = max(r.x1, l.x1), min(r.y0, l.y0), max(r.y1, l.y1)
        out.append(r)
    return out


def tidy(blocks):
    """Collapses stray scene breaks and rejoins paragraphs split mid-sentence at page turns."""
    out = []
    for b in blocks:
        if b[0] == 'scene':
            if not out or out[-1][0] in ('scene', 'heading'):
                continue
        elif b[0] == 'heading':
            if out and out[-1][0] == 'scene':
                out.pop()
        elif b[0] == 'para':
            _, text, first, spaced = b
            if not first and not spaced and out and out[-1][0] == 'para' \
                    and re.search(r'[a-z,]$', out[-1][1]) and text[:1].islower():
                _, pt, pf, ps = out[-1]
                out[-1] = ('para', pt + '\n' + text, pf, ps)
                continue
        out.append(b)
    if out and out[-1][0] == 'scene':
        out.pop()
    return out


def build(pages, pdf, language, body_range=None):
    """Returns (blocks, body_start, body_end)."""
    for p in pages:
        p.lines = [clean_line(l) for l in p.lines]
    strip_running_heads(pages)
    n = len(pages)

    # body = first .. last page densely filled with full-width lines
    dense = [p.index for p in pages if sum(1 for l in p.lines if l.width > 0.5) >= 10]
    start = body_range[0] if body_range else max(1, dense[0] if dense else 1)
    end = body_range[1] if body_range else (dense[-1] if dense else n - 1)
    start, end = min(start, n - 1), min(max(end, start), n - 1)
    # a short final story page still belongs to the body unless it opens something new
    while body_range is None and end + 1 < n - 1:
        rows = merge_rows(pages[end + 1].lines)
        if rows and rows[0].width > 0.5 and (not is_heading_text(rows[0].text) or len(rows[0].text) > 60):
            end += 1
        else:
            break

    # word counts across the whole book, used to restore drop-cap initials ("ia" -> "Dia")
    vocab = {}
    for p in pages:
        for l in p.lines:
            for w in re.split(r'[^\p{L}]+', l.text.lower()):
                if w:
                    vocab[w] = vocab.get(w, 0) + 1

    def restore_initial(t):
        word = re.match(r'^\p{L}*', t).group(0).lower()
        if not word:
            return None
        best = max('ABCDEFGHIJKLMNOPQRSTUVWXYZ', key=lambda c: vocab.get(c.lower() + word, 0))
        return best if vocab.get(best.lower() + word, 0) > 0 else None

    body = pages[start:end + 1]
    wide = [l for p in body for l in p.lines if l.width > 0.5]
    lh = median([l.height for l in wide]) or 0.02
    parity_margin, parity_right = [0.1, 0.1], [0.9, 0.9]
    for parity in (0, 1):
        ls = [l for p in body if p.index % 2 == parity for l in p.lines if l.width > 0.5]
        m = percentile([l.x0 for l in ls], 0.1)
        r = percentile([l.x1 for l in ls], 0.75)
        if m is not None:
            parity_margin[parity] = m
        if r is not None:
            parity_right[parity] = r
    gaps = []
    for p in body:
        ls = merge_rows([l for l in p.lines if l.width > 0.5])
        gaps += [b.y0 - a.y1 for a, b in zip(ls, ls[1:]) if -0.01 < b.y0 - a.y1 < 0.05]
    normal_gap = median(gaps) or 0.007
    para_gap = normal_gap + 0.5 * lh
    scene_gap = normal_gap + 1.6 * lh

    blocks = []
    cur = None      # [text, first, spaced, ended]

    def close():
        nonlocal cur
        if cur:
            blocks.append(('para', cur[0], cur[1], cur[2]))
        cur = None

    for i in range(1, start):
        blocks.append(('image', i))

    for i in range(start, end + 1):
        raw = list(pages[i].lines)
        wide_here = [l for l in raw if l.width > 0.5]
        margin = percentile([l.x0 for l in wide_here], 0.1) if len(wide_here) >= 5 else parity_margin[i % 2]
        right = percentile([l.x1 for l in wide_here], 0.75) if len(wide_here) >= 5 else parity_right[i % 2]
        text_width = right - margin
        center = (margin + right) / 2

        if not raw or (len(raw) <= 2 and all(len(l.text) < 4 for l in raw)):
            close()
            blocks.append(('image', i))
            continue

        # lone big letters = drop caps; pull them out before rows are merged
        initials = []
        kept = []
        for l in raw:
            t = l.text.strip()
            if len(t) == 1 and t.isupper() and l.height > 1.4 * lh and l.x0 < margin + 0.05:
                initials.append((t, l.y0, l.y1))
            else:
                kept.append(l)
        raw = kept
        # OCR sometimes fuses the drop cap with the second line ("IV tubuh…" for "M" + "tubuh…"):
        # keep only the second line's text, positioned where that line really is
        fixed = []
        for l in raw:
            sp = l.text.find(' ')
            if l.y0 < 0.5 and l.height > 1.6 * lh and l.width > 0.3 and 0 <= sp <= 2:
                m = l.copy()
                m.text = l.text[sp + 1:]
                m.y0 = l.y1 - lh
                m.x0 = l.x0 + 0.05
                fixed.append(m)
            else:
                fixed.append(l)
        rows = merge_rows(fixed)

        # heading rows at the top of the page
        heading_rows = 0
        for k, r in enumerate(rows[:3]):
            if not (r.y0 < 0.3 and is_heading_text(r.text) and (r.width < 0.75 * text_width or r.height > 1.3 * lh)):
                break
            next_gap = rows[k + 1].y0 - r.y1 if k + 1 < len(rows) else 1
            heading_rows = k + 1
            if next_gap > scene_gap:
                break
            if k + 1 < len(rows) and not is_heading_text(rows[k + 1].text):
                heading_rows = 0
                break
        if heading_rows > 0:
            last = rows[heading_rows - 1]
            gap_after = rows[heading_rows].y0 - last.y1 if heading_rows < len(rows) else 1
            big = any(r.height > 1.3 * lh for r in rows[:heading_rows])
            if not (gap_after > scene_gap or big):
                heading_rows = 0

        prev = None
        drop_cap_pending = False
        skip_indent_check = False
        for k, r in enumerate(rows):
            t = r.text
            gap = r.y0 - prev.y1 if prev else 0

            if k < heading_rows:
                if k == 0:
                    close()
                title = pretty_heading(t)
                if k > 0 and blocks and blocks[-1][0] == 'heading':
                    blocks[-1] = ('heading', blocks[-1][1] + ' — ' + title)
                else:
                    blocks.append(('heading', title))
                drop_cap_pending = True
                prev = r
                continue
            is_centered = (r.x0 > margin + 0.12 * text_width and r.x1 < right - 0.1 * text_width
                           and abs(r.mid - center) < 0.06 * text_width)
            if t.strip() in ORNAMENTS and (is_centered or r.width < 0.1):
                close()
                blocks.append(('scene',))
                prev = None
                continue
            if prev is not None and gap > scene_gap and not drop_cap_pending:
                close()
                blocks.append(('scene',))
            if is_centered:
                close()
                if blocks and blocks[-1][0] == 'centered' and prev is not None and gap < para_gap:
                    blocks[-1] = ('centered', blocks[-1][1] + [t])
                else:
                    blocks.append(('centered', [t]))
                prev = r
                continue

            indented = r.x0 - margin > 0.025
            if drop_cap_pending or (k == 0 and i == start):
                # first paragraph after a heading: restore a drop-cap initial if OCR lost it
                close()
                nxt = rows[k + 1] if k + 1 < len(rows) else None
                cap_layout = nxt is not None and nxt.x0 - margin > 0.025
                if cap_layout:
                    # noise OCR made of the big initial, e.g. ", arah"
                    t = re.sub(r'^[^\p{L}"“‘\'(]+', '', t)
                    skip_indent_check = True
                ini = next((x for x in initials if x[1] < r.y1 + 0.01 and x[2] > r.y0 - 0.01), None)
                if ini:
                    t = ini[0] + t
                    skip_indent_check = True
                elif re.match(r'^\p{Lu} (?=\p{Ll})', t) and \
                        vocab.get((t[0] + re.match(r'^\p{L}*', t[2:]).group(0)).lower(), 0) > 0:
                    t = t[0] + t[2:]        # "B udi" -> "Budi"
                elif t[:1].islower():
                    # a chapter never opens lowercase: the initial was lost
                    found = restore_initial(t)
                    if not found:
                        found = read_initial(pdf, i, (max(0, margin - 0.02), max(0, r.y0 - 0.01),
                                                      max(0.02, r.x0 - margin + 0.015),
                                                      (nxt.y1 if nxt else r.y1) - r.y0 + 0.02), language)
                    if found:
                        t = found + t
                cur = [t, drop_cap_pending, False, r.x1 < right - 0.06]
                drop_cap_pending = False
                prev = r
                continue
            if skip_indent_check:
                # second line beside the drop cap
                skip_indent_check = False
                if cur is not None:
                    cur[0] += '\n' + t
                    cur[3] = r.x1 < right - 0.06
                    prev = r
                    continue

            starts_new = cur is None or indented or (prev is not None and gap > para_gap) or (cur is not None and cur[3])
            if prev is None and not indented and cur is not None and not cur[3]:
                starts_new = False      # continues from the previous page
            if starts_new:
                close()
                cur = [t, False, prev is not None and gap > para_gap and not indented, False]
            else:
                cur[0] += '\n' + t
            cur[3] = r.x1 < right - 0.06
            prev = r
    close()
    for i in range(end + 1, n):
        blocks.append(('image', i))
    return tidy(blocks), start, end


# MARK: - Cleaning paragraph text

class TextFixer:
    """Rejoins lines, undoes typesetting hyphens, fixes quotes."""

    def __init__(self, blocks):
        self.vocab = {}
        for b in blocks:
            if b[0] == 'para':
                for m in re.finditer(r'\p{L}+(?:-\p{L}+)*', b[1].replace('\n', ' ')):
                    w = m.group(0).lower()
                    self.vocab[w] = self.vocab.get(w, 0) + 1

    def join_lines(self, t):
        parts = t.split('\n')
        out = parts[0]
        for nxt in parts[1:]:
            m = re.search(r'\p{L}+-$', out) if out.endswith('-') else None
            if m:
                a = m.group(0)[:-1]
                b = re.match(r'^\p{L}*', nxt).group(0)
                hy, joined = ('%s-%s' % (a, b)).lower(), (a + b).lower()
                la, lb = a.lower(), b.lower()
                redup = bool(b) and (lb.startswith(la[:3]) or la.startswith(lb[:3]))
                if self.vocab.get(hy, 0) > self.vocab.get(joined, 0) \
                        or (self.vocab.get(joined, 0) == 0 and redup) or b[:1].isupper():
                    out += nxt                  # real hyphen (reduplication / compound)
                else:
                    out = out[:-1] + nxt        # typesetting hyphen
            elif out and nxt and (joins_without_space(out[-1]) or joins_without_space(nxt[0])):
                out += nxt                      # Japanese, Chinese and Thai lines join without a space
            else:
                out += ' ' + nxt
        return out

    def finalize(self, blocks):
        out = []
        for b in blocks:
            if b[0] == 'para':
                out.append(('para', self.fix(b[1]), b[2], b[3]))
            elif b[0] == 'centered':
                out.append(('centered', [self.fix(l) for l in b[1]]))
            else:
                out.append(b)
        return out

    def fix(self, text):
        t = self.join_lines(text)

        def sub(p, r):
            nonlocal t
            t = re.sub(p, r, t)
        sub(r'\[\s?\]', '')
        sub(r'(\w{3,})ii(?=[\s,.]|$)', r'\1"')          # closing quote read as "ii"
        sub(r'\s+([,.!?;:])(?=\s|$|")', r'\1')
        sub(r'\.\s?\.\s?\.(\s?\.)?', '…')
        sub(r'…+', '…')
        t = t.replace('®', '"').replace('›', '')
        sub(r'^[«#]', '"')
        sub(r'\s*I?\]\s*$', '')                         # end-of-chapter box
        sub(r'\s{2,}', ' ')
        t = t.strip()
        # smart quotes
        sub(r'(^|[\s(\[—–-])"', '\\1“')
        t = t.replace('"', '”')
        sub(r"(^|[\s(“])'", '\\1‘')
        t = t.replace("'", '’')
        return t
