import AppKit

// A small command-line tool built from the Mac app's own KindleFormat.swift and AppIcon.swift, for macOS
// versions older than the app needs (GitHub's Mac runners). The same flags as the app:
//   --icon <dir>   --to-azw3 <in.epub> <out.azw3> [asin]   --to-epub <in.azw3|mobi> <out.epub>
let args = CommandLine.arguments
if let i = args.firstIndex(of: "--icon"), i + 1 < args.count {
    AppIcon.render(to: URL(fileURLWithPath: args[i + 1]))
    exit(0)
}
for flag in ["--to-azw3", "--to-epub"] {
    guard let i = args.firstIndex(of: flag), i + 2 < args.count else { continue }
    let src = URL(fileURLWithPath: args[i + 1]), out = URL(fileURLWithPath: args[i + 2])
    do {
        if flag == "--to-azw3" {
            try KindleBook.write(epub: src, to: out, asin: i + 3 < args.count ? args[i + 3] : UUID().uuidString)
        } else {
            try KindleRead.toEPUB(src, to: out)
        }
        print("written \(out.path)")
        exit(0)
    } catch {
        print("error: \(error.localizedDescription)")
        exit(1)
    }
}
print("usage: --icon <dir> | --to-azw3 <in> <out> [asin] | --to-epub <in> <out>")
exit(2)
