import Foundation
import PDFKit
import Vision
import CoreGraphics

/// One line of text on a page. Coordinates are normalised 0...1 with y growing downwards.
struct Line {
    var text: String
    var x0: Double, x1: Double, y0: Double, y1: Double
    var width: Double { x1 - x0 }
    var height: Double { y1 - y0 }
    var mid: Double { (x0 + x1) / 2 }
}

struct PageText {
    var index: Int
    var lines: [Line]
    /// A large initial letter found on its own (drop cap), with the y-range it spans.
    var dropCaps: [(letter: String, y0: Double, y1: Double, x1: Double)] = []
}

enum PDFReader {
    /// Renders a page to a CGImage at the given DPI (white background).
    static func render(_ page: PDFPage, dpi: Double, gray: Bool = false, crop: CGRect? = nil) -> CGImage? {
        let box = page.bounds(for: .mediaBox)
        let scale = dpi / 72
        // crop is normalised, top-down
        let region = crop.map { CGRect(x: box.minX + $0.minX * box.width,
                                      y: box.minY + (1 - $0.maxY) * box.height,
                                      width: $0.width * box.width, height: $0.height * box.height) } ?? box
        let w = max(1, Int(region.width * scale)), h = max(1, Int(region.height * scale))
        let space = gray ? CGColorSpaceCreateDeviceGray() : CGColorSpaceCreateDeviceRGB()
        let info = gray ? CGImageAlphaInfo.none.rawValue : CGImageAlphaInfo.noneSkipLast.rawValue
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: space, bitmapInfo: info) else { return nil }
        ctx.setFillColor(gray ? CGColor(gray: 1, alpha: 1) : CGColor(red: 1, green: 1, blue: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        ctx.interpolationQuality = .high
        ctx.scaleBy(x: scale, y: scale)
        ctx.translateBy(x: -region.minX, y: -region.minY)
        if let ref = page.pageRef {
            ctx.drawPDFPage(ref)
        }
        return ctx.makeImage()
    }

    static func ocr(_ image: CGImage, languages: [String], correction: Bool = true) -> [Line] {
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.recognitionLanguages = languages
        req.usesLanguageCorrection = correction
        try? VNImageRequestHandler(cgImage: image, options: [:]).perform([req])
        return (req.results ?? []).compactMap { o in
            guard let c = o.topCandidates(1).first else { return nil }
            let b = o.boundingBox
            return Line(text: c.string, x0: b.minX, x1: b.maxX, y0: 1 - b.maxY, y1: 1 - b.minY)
        }
    }

    /// Lines from the PDF's own text layer, if it has one.
    static func textLayer(_ page: PDFPage) -> [Line]? {
        guard let s = page.string, s.trimmingCharacters(in: .whitespacesAndNewlines).count > 40 else { return nil }
        let box = page.bounds(for: .mediaBox)
        guard let sel = page.selection(for: box) else { return nil }
        var out: [Line] = []
        for l in sel.selectionsByLine() {
            let t = (l.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if t.isEmpty { continue }
            let b = l.bounds(for: page)
            out.append(Line(text: t,
                            x0: (b.minX - box.minX) / box.width, x1: (b.maxX - box.minX) / box.width,
                            y0: 1 - (b.maxY - box.minY) / box.height, y1: 1 - (b.minY - box.minY) / box.height))
        }
        return out
    }

    /// Reads every page, OCRing scanned pages. `progress` is called with pages done.
    static func read(_ doc: PDFDocument, languages: [String], forceOCR: Bool,
                     progress: @escaping @Sendable (Int) -> Void) async -> [PageText] {
        let n = doc.pageCount
        let url = doc.documentURL
        var results = [PageText?](repeating: nil, count: n)
        let workers = max(2, ProcessInfo.processInfo.activeProcessorCount - 2)
        await withTaskGroup(of: PageText.self) { group in
            var next = 0
            var done = 0
            func add() {
                let i = next
                next += 1
                group.addTask {
                    // each task opens its own document: PDFKit isn't safe to share across threads
                    let d = url.flatMap { PDFDocument(url: $0) }
                    guard let page = d?.page(at: i) else { return PageText(index: i, lines: []) }
                    if !forceOCR, let lines = textLayer(page) { return PageText(index: i, lines: lines) }
                    guard let img = render(page, dpi: 200, gray: true) else { return PageText(index: i, lines: []) }
                    return PageText(index: i, lines: ocr(img, languages: languages))
                }
            }
            for _ in 0..<min(workers, n) { add() }
            for await r in group {
                results[r.index] = r
                done += 1
                progress(done)
                if next < n { add() }
            }
        }
        return results.map { $0! }
    }

    /// Tries to read a drop-cap initial from a cropped region (normalised, top-down).
    static func readInitial(_ page: PDFPage, region: CGRect, languages: [String]) -> String? {
        guard let img = render(page, dpi: 300, gray: true, crop: region) else { return nil }
        let text = ocr(img, languages: languages, correction: false).map(\.text).joined()
        guard let c = text.first(where: { $0.isLetter }), c.isUppercase else { return nil }
        return String(c)
    }
}
