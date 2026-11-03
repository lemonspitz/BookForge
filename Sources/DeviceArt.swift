import SwiftUI

/// A drawing of the connected e-reader, shaped after its family, with a book cover on its screen.
struct DeviceImage: View {
    var kind: String
    var cover: NSImage? = nil

    private struct Spec {
        var aspect: CGFloat                  // width / height
        var body: Color
        var corner: CGFloat                  // × width
        var inset: (top: CGFloat, leading: CGFloat, bottom: CGFloat, trailing: CGFloat)   // × width
        var controls: Controls = .none
    }
    private enum Controls { case none, grip, homeButton, buttonRow }

    private var spec: Spec {
        switch kind {
        case "kindle-oasis":
            Spec(aspect: 0.80, body: Color(white: 0.22), corner: 0.05, inset: (0.06, 0.06, 0.06, 0.24), controls: .grip)
        case "kindle-scribe":
            Spec(aspect: 0.82, body: Color(white: 0.30), corner: 0.035, inset: (0.045, 0.045, 0.045, 0.13))
        case "kindle":
            Spec(aspect: 0.70, body: Color(white: 0.11), corner: 0.06, inset: (0.075, 0.07, 0.13, 0.07))
        case "kobo":
            Spec(aspect: 0.72, body: Color(white: 0.08), corner: 0.05, inset: (0.065, 0.06, 0.07, 0.06))
        case "pocketbook":
            Spec(aspect: 0.67, body: Color(red: 0.27, green: 0.28, blue: 0.31), corner: 0.06, inset: (0.08, 0.07, 0.19, 0.07), controls: .buttonRow)
        case "boox":
            Spec(aspect: 0.73, body: Color(white: 0.17), corner: 0.045, inset: (0.05, 0.05, 0.11, 0.05), controls: .homeButton)
        default:
            Spec(aspect: 0.70, body: Color(white: 0.30), corner: 0.06, inset: (0.07, 0.07, 0.11, 0.07), controls: .homeButton)
        }
    }

    var body: some View {
        let s = spec
        GeometryReader { g in
            let w = min(g.size.width, g.size.height * s.aspect)
            let h = w / s.aspect
            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: w * s.corner, style: .continuous)
                    .fill(LinearGradient(colors: [s.body.opacity(0.85), s.body], startPoint: .top, endPoint: .bottom))
                    .overlay(RoundedRectangle(cornerRadius: w * s.corner, style: .continuous)
                        .strokeBorder(.white.opacity(0.18), lineWidth: max(0.5, w * 0.006)))
                    .shadow(color: .black.opacity(0.3), radius: w * 0.04, y: w * 0.02)
                screen(width: w * (1 - s.inset.leading - s.inset.trailing),
                       height: h - w * (s.inset.top + s.inset.bottom))
                    .offset(x: w * s.inset.leading, y: w * s.inset.top)
                controls(s, w: w, h: h)
            }
            .frame(width: w, height: h)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .aspectRatio(s.aspect, contentMode: .fit)
        .accessibilityLabel("E-reader")
    }

    private func screen(width: CGFloat, height: CGFloat) -> some View {
        ZStack {
            Rectangle().fill(LinearGradient(colors: [Color(white: 0.93), Color(white: 0.88)],
                                            startPoint: .topLeading, endPoint: .bottomTrailing))
            if let cover {
                Image(nsImage: cover).resizable().aspectRatio(contentMode: .fill)
                    .grayscale(1).contrast(0.9).brightness(0.04)
                    .frame(width: width, height: height).clipped()
            } else if width > 40 {
                // a page of text
                VStack(alignment: .leading, spacing: height * 0.035) {
                    ForEach(0..<9, id: \.self) { i in
                        Capsule().fill(Color(white: 0.62))
                            .frame(width: width * (i == 8 ? 0.45 : i == 0 ? 0.6 : 0.78), height: max(1, height * 0.012))
                    }
                }
            }
        }
        .frame(width: width, height: height)
        .clipShape(RoundedRectangle(cornerRadius: width * 0.012))
    }

    @ViewBuilder
    private func controls(_ s: Spec, w: CGFloat, h: CGFloat) -> some View {
        let buttonColor = Color.white.opacity(0.16)
        switch s.controls {
        case .grip:
            // two page-turn buttons on the wide side
            let x = w * (1 - s.inset.trailing / 2) - w * 0.012
            ForEach(0..<2, id: \.self) { i in
                Capsule().fill(buttonColor)
                    .frame(width: w * 0.024, height: h * 0.1)
                    .offset(x: x, y: h * (0.36 + 0.15 * CGFloat(i)))
            }
        case .homeButton:
            Capsule().fill(buttonColor)
                .frame(width: w * 0.16, height: w * 0.03)
                .offset(x: w * 0.42, y: h - w * s.inset.bottom / 2 - w * 0.015)
        case .buttonRow:
            let y = h - w * s.inset.bottom / 2 - w * 0.03
            HStack(spacing: w * 0.05) {
                Capsule().fill(buttonColor).frame(width: w * 0.12, height: w * 0.035)
                Capsule().fill(buttonColor).frame(width: w * 0.12, height: w * 0.035)
                Circle().fill(buttonColor).frame(width: w * 0.06, height: w * 0.06)
                Capsule().fill(buttonColor).frame(width: w * 0.12, height: w * 0.035)
                Capsule().fill(buttonColor).frame(width: w * 0.12, height: w * 0.035)
            }
            .frame(width: w)
            .offset(y: y)
        case .none:
            EmptyView()
        }
    }

    static func familyName(_ kind: String) -> String {
        switch kind {
        case "kindle", "kindle-oasis", "kindle-scribe": "Amazon Kindle"
        case "kobo": "Kobo"
        case "pocketbook": "PocketBook"
        case "boox": "Onyx Boox"
        case "tolino": "Tolino"
        case "nook": "Barnes & Noble Nook"
        default: "E-reader"
        }
    }
}
