import Foundation
import PDFKit

/// Structural pieces of the rebuilt book.
enum Block {
    case heading(String)
    case para(text: String, first: Bool, spaced: Bool)
    case centered([String])
    case scene
    case pageImage(Int)
}

struct LayoutResult {
    var blocks: [Block]
    var bodyStart: Int
    var bodyEnd: Int
}

/// Turns positioned lines into paragraphs, headings and scene breaks.
/// Pure heuristics: indents start paragraphs, big gaps are scene breaks,
/// a short unpunctuated line at the top of a page followed by space is a heading.
enum Layout {
    static let ornaments: Set<String> = ["COS", "CoS", "cos", "cg", "Cg", "C3", "c3", "ca", "CG", "CS", "cs",
                                         "*", "* * *", "***", "~", "#", "☙", "❧", "§", "o0o", "oOo", "-o-"]

    static func build(pages: [PageText], doc: PDFDocument, languages: [String],
                      bodyRange: ClosedRange<Int>? = nil) -> LayoutResult {
        var pages = pages.map { var p = $0; p.lines = p.lines.map(cleanLine); return p }
        stripRunningHeads(&pages)

        // body = first .. last page densely filled with full-width lines
        let dense = pages.filter { p in p.lines.filter { $0.width > 0.5 }.count >= 10 }.map(\.index)
        let start = bodyRange?.lowerBound ?? max(1, dense.first ?? 1)
        var end = bodyRange?.upperBound ?? (dense.last ?? pages.count - 1)
        // a short final story page still belongs to the body unless it opens something new
        while bodyRange == nil, end + 1 < pages.count - 1,
              let first = mergeRows(pages[end + 1].lines).first,
              first.width > 0.5, !isHeadingText(first.text) || first.text.count > 60 {
            end += 1
        }

        // word counts across the whole book, used to restore drop-cap initials ("ia" -> "Dia")
        var vocab: [String: Int] = [:]
        for p in pages {
            for l in p.lines {
                for w in l.text.lowercased().split(whereSeparator: { !$0.isLetter }) { vocab[String(w), default: 0] += 1 }
            }
        }
        func restoreInitial(_ t: String) -> String? {
            let word = t.prefix { $0.isLetter }.lowercased()
            guard !word.isEmpty else { return nil }
            let best = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".map { c in (c, vocab[String(c).lowercased() + word, default: 0]) }
                .max { $0.1 < $1.1 }!
            return best.1 > 0 ? String(best.0) : nil
        }

        // global metrics from body text
        let wide = pages[start...end].flatMap { $0.lines.filter { $0.width > 0.5 } }
        let lh = median(wide.map(\.height)) ?? 0.02
        var parityMargin = [0.1, 0.1], parityRight = [0.9, 0.9]
        for parity in 0...1 {
            let ls = pages[start...end].filter { $0.index % 2 == parity }.flatMap { $0.lines.filter { $0.width > 0.5 } }
            if let m = percentile(ls.map(\.x0), 0.1) { parityMargin[parity] = m }
            if let r = percentile(ls.map(\.x1), 0.75) { parityRight[parity] = r }
        }
        let normalGap = median(pages[start...end].flatMap { p -> [Double] in
            let ls = mergeRows(p.lines.filter { $0.width > 0.5 })
            return zip(ls, ls.dropFirst()).map { $1.y0 - $0.y1 }.filter { $0 > -0.01 && $0 < 0.05 }
        }) ?? 0.007
        let paraGap = normalGap + 0.5 * lh
        let sceneGap = normalGap + 1.6 * lh

        var blocks: [Block] = []
        var cur: (text: String, first: Bool, spaced: Bool, ended: Bool)?
        func close() {
            if let c = cur { blocks.append(.para(text: c.text, first: c.first, spaced: c.spaced)) }
            cur = nil
        }

        for i in 1..<start { blocks.append(.pageImage(i)) }

        for i in start...end {
            var raw = pages[i].lines
            let wideHere = raw.filter { $0.width > 0.5 }
            let margin = wideHere.count >= 5 ? percentile(wideHere.map(\.x0), 0.1)! : parityMargin[i % 2]
            let right = wideHere.count >= 5 ? percentile(wideHere.map(\.x1), 0.75)! : parityRight[i % 2]
            let textWidth = right - margin
            let center = (margin + right) / 2

            if raw.isEmpty || (raw.count <= 2 && raw.allSatisfy { $0.text.count < 4 }) {
                close()
                blocks.append(.pageImage(i))
                continue
            }

            // lone big letters = drop caps; pull them out before rows are merged
            var initials: [(letter: String, y0: Double, y1: Double)] = []
            raw.removeAll { l in
                let t = l.text.trimmingCharacters(in: .whitespaces)
                if t.count == 1, t.first!.isUppercase, l.height > 1.4 * lh, l.x0 < margin + 0.05 {
                    initials.append((t, l.y0, l.y1))
                    return true
                }
                return false
            }
            // Vision sometimes fuses the drop cap with the second line ("IV tubuh…" for "M" + "tubuh…"):
            // keep only the second line's text, positioned where that line really is
            raw = raw.map { l in
                guard l.y0 < 0.5, l.height > 1.6 * lh, l.width > 0.3,
                      let sp = l.text.firstIndex(of: " "), l.text.distance(from: l.text.startIndex, to: sp) <= 2 else { return l }
                var m = l
                m.text = String(l.text[l.text.index(after: sp)...])
                m.y0 = l.y1 - lh
                m.x0 = l.x0 + 0.05
                return m
            }
            let rows = mergeRows(raw)

            // heading rows at the top of the page
            var headingRows = 0
            for (k, r) in rows.prefix(3).enumerated() {
                guard r.y0 < 0.3, isHeadingText(r.text), r.width < 0.75 * textWidth || r.height > 1.3 * lh else { break }
                let nextGap = k + 1 < rows.count ? rows[k + 1].y0 - r.y1 : 1
                headingRows = k + 1
                if nextGap > sceneGap { break }
                if k + 1 < rows.count, !isHeadingText(rows[k + 1].text) { headingRows = 0; break }
            }
            if headingRows > 0 {
                let last = rows[headingRows - 1]
                let gapAfter = headingRows < rows.count ? rows[headingRows].y0 - last.y1 : 1
                let big = rows.prefix(headingRows).contains { $0.height > 1.3 * lh }
                if !(gapAfter > sceneGap || big) { headingRows = 0 }
            }

            var prev: Line?
            var dropCapPending = false
            var skipIndentCheck = false
            for (k, r) in rows.enumerated() {
                var t = r.text
                let gap = prev.map { r.y0 - $0.y1 } ?? 0

                if k < headingRows {
                    if k == 0 { close() }
                    let title = prettyHeading(t)
                    if k > 0, case .heading(let h)? = blocks.last {
                        blocks[blocks.count - 1] = .heading(h + " — " + title)
                    } else {
                        blocks.append(.heading(title))
                    }
                    dropCapPending = true
                    prev = r
                    continue
                }
                let isCentered = r.x0 > margin + 0.12 * textWidth && r.x1 < right - 0.1 * textWidth
                    && abs(r.mid - center) < 0.06 * textWidth
                if ornaments.contains(t.trimmingCharacters(in: .whitespaces)) && (isCentered || r.width < 0.1) {
                    close()
                    blocks.append(.scene)
                    prev = nil
                    continue
                }
                if prev != nil, gap > sceneGap, !dropCapPending {
                    close()
                    blocks.append(.scene)
                }
                if isCentered {
                    close()
                    if case .centered(var ls)? = blocks.last, prev != nil, gap < paraGap {
                        ls.append(t)
                        blocks[blocks.count - 1] = .centered(ls)
                    } else {
                        blocks.append(.centered([t]))
                    }
                    prev = r
                    continue
                }

                let indented = r.x0 - margin > 0.025
                if dropCapPending || (k == 0 && i == start) {
                    // first paragraph after a heading: restore a drop-cap initial if OCR lost it
                    close()
                    let next = k + 1 < rows.count ? rows[k + 1] : nil
                    let capLayout = next.map { $0.x0 - margin > 0.025 } ?? false
                    if capLayout {
                        // noise OCR made of the big initial, e.g. ", arah"
                        t = t.replacingOccurrences(of: #"^[^\p{L}"“‘'(]+"#, with: "", options: .regularExpression)
                        skipIndentCheck = true
                    }
                    if let ini = initials.first(where: { $0.y0 < r.y1 + 0.01 && $0.y1 > r.y0 - 0.01 }) {
                        t = ini.letter + t
                        skipIndentCheck = true
                    } else if let m = t.range(of: #"^\p{Lu} (?=\p{Ll})"#, options: .regularExpression),
                              vocab[(String(t.prefix(1)) + t.dropFirst(2).prefix { $0.isLetter }).lowercased(), default: 0] > 0 {
                        t.replaceSubrange(m, with: String(t.prefix(1)))   // "B udi" -> "Budi"
                    } else if t.first?.isLowercase ?? false {
                        // a chapter never opens lowercase: the initial was lost
                        if let ini = restoreInitial(t) {
                            t = ini + t
                        } else if let page = doc.page(at: i),
                                  let ini = PDFReader.readInitial(page, region: CGRect(
                                    x: max(0, margin - 0.02), y: max(0, r.y0 - 0.01),
                                    width: max(0.02, r.x0 - margin + 0.015), height: (next?.y1 ?? r.y1) - r.y0 + 0.02),
                                    languages: languages) {
                            t = ini + t
                        }
                    }
                    cur = (t, dropCapPending, false, r.x1 < right - 0.06)
                    dropCapPending = false
                    prev = r
                    continue
                }
                if skipIndentCheck {
                    // second line beside the drop cap
                    skipIndentCheck = false
                    if cur != nil {
                        cur!.text += "\n" + t
                        cur!.ended = r.x1 < right - 0.06
                        prev = r
                        continue
                    }
                }

                var startsNew = cur == nil || indented || (prev != nil && gap > paraGap) || (cur?.ended ?? false)
                if prev == nil, !indented, let c = cur, !c.ended { startsNew = false } // continues from previous page
                if startsNew {
                    close()
                    cur = (t, false, prev != nil && gap > paraGap && !indented, false)
                } else {
                    cur!.text += "\n" + t
                }
                cur!.ended = r.x1 < right - 0.06
                prev = r
            }
        }
        close()
        if end + 1 < pages.count {
            for i in (end + 1)..<pages.count { blocks.append(.pageImage(i)) }
        }
        return LayoutResult(blocks: tidy(blocks), bodyStart: start, bodyEnd: end)
    }

    // MARK: helpers

    static let homoglyphs: [Character: Character] = ["а": "a", "р": "p", "К": "K", "е": "e", "п": "n", "ı": "i",
                                                     "‚": ",", "¿": "?", "ạ": "a", "ị": "i", "о": "o", "с": "c"]

    static func cleanLine(_ l: Line) -> Line {
        var l = l
        l.text = String(l.text.trimmingCharacters(in: .whitespaces).map { homoglyphs[$0] ?? $0 })
        return l
    }

    static func isHeadingText(_ t: String) -> Bool {
        let s = t.trimmingCharacters(in: .whitespaces)
        guard s.count >= 1, s.count <= 60, let f = s.first else { return false }
        if ornaments.contains(s) { return false }
        if let l = s.last, ".,;!?\"”’…-".contains(l), !s.hasSuffix("?!") { return false }
        return f.isUppercase || f.isNumber
    }

    static func prettyHeading(_ t: String) -> String {
        let letters = t.filter(\.isLetter)
        return !letters.isEmpty && letters == letters.uppercased() ? t.capitalized : t
    }

    /// Removes page numbers and running heads/feet repeated across pages.
    static func stripRunningHeads(_ pages: inout [PageText]) {
        func key(_ s: String) -> String { s.lowercased().filter { $0.isLetter } }
        var counts: [String: Int] = [:]
        for p in pages {
            for l in p.lines where l.y1 < 0.07 || l.y0 > 0.93 { counts[key(l.text), default: 0] += 1 }
        }
        let limit = max(3, pages.count / 10)
        for i in pages.indices {
            pages[i].lines.removeAll { l in
                guard l.y1 < 0.07 || l.y0 > 0.93 else { return false }
                let s = l.text.trimmingCharacters(in: .whitespaces)
                if s.range(of: #"^[\divxlcIVXLC\-–— ]{1,8}$"#, options: .regularExpression) != nil { return true }
                return counts[key(s), default: 0] >= limit && !key(s).isEmpty
            }
        }
    }

    /// Vision may split a justified line into fragments; rejoin pieces on the same row.
    static func mergeRows(_ lines: [Line]) -> [Line] {
        var groups: [[Line]] = []
        for l in lines.sorted(by: { $0.y0 < $1.y0 }) {
            if let g = groups.last, abs(l.y0 - g[0].y0) < 0.4 * max(0.012, g[0].height) {
                groups[groups.count - 1].append(l)
            } else {
                groups.append([l])
            }
        }
        return groups.map { g in
            let s = g.sorted { $0.x0 < $1.x0 }
            var r = s[0]
            for l in s.dropFirst() {
                r.text += " " + l.text
                r.x1 = max(r.x1, l.x1); r.y0 = min(r.y0, l.y0); r.y1 = max(r.y1, l.y1)
            }
            return r
        }
    }

    /// Collapses stray scene breaks and rejoins paragraphs split mid-sentence at page turns.
    static func tidy(_ blocks: [Block]) -> [Block] {
        var out: [Block] = []
        for b in blocks {
            switch b {
            case .scene:
                if case .scene? = out.last { continue }
                if case .heading? = out.last { continue }
                if out.isEmpty { continue }
            case .heading:
                if case .scene? = out.last { out.removeLast() }
            case .para(let text, let first, let spaced):
                if !first, !spaced, case .para(let pt, let pf, let ps)? = out.last,
                   pt.range(of: #"[a-z,]$"#, options: .regularExpression) != nil,
                   text.first?.isLowercase ?? false {
                    out[out.count - 1] = .para(text: pt + "\n" + text, first: pf, spaced: ps)
                    continue
                }
            default: break
            }
            out.append(b)
        }
        if case .scene? = out.last { out.removeLast() }
        return out
    }

    static func median(_ xs: [Double]) -> Double? { percentile(xs, 0.5) }
    static func percentile(_ xs: [Double], _ p: Double) -> Double? {
        guard !xs.isEmpty else { return nil }
        let s = xs.sorted()
        return s[min(s.count - 1, Int(Double(s.count) * p))]
    }
}
