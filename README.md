# Book Forge

A small macOS app that turns PDF books into clean EPUBs, translates them between Indonesian, English and Japanese, and sends them to a Kindle with the cover showing in the Kindle library.

## What it does

- **PDF → EPUB.** Uses the PDF's text layer when it has one, otherwise reads scanned pages with Apple's on-device text recognition (Indonesian, English, Japanese). It rebuilds chapters, paragraphs, scene breaks and drop caps, removes running heads and page numbers, and keeps the cover and any image pages.
- **Translate an EPUB.** Translates the whole book while keeping italics, bold, links, images and the table of contents.
  - **Claude** (default, needs an Anthropic API key): reads passages from across the book first and writes a style sheet (the narrator's voice, how each character speaks, fixed spellings for names and titles), then translates with per-language-pair guidance on slang, honorifics, politeness and dialogue punctuation. The app shows a cost estimate and asks before sending anything (roughly $2–5 for a novel). Your key is kept in the macOS Keychain.
  - **Apple Translate**: free and offline, but literal.
- **Send to Kindle.**
  - **USB**: converts to AZW3 with Calibre and uploads it the way Calibre's "Send to device" does, so the cover shows on the Kindle home screen.
  - **Wi-Fi**: emails the EPUB to your Kindle's `@kindle.com` address through the Mail app. Add your sending address to the approved list in your Amazon account first.

## Requirements

- macOS 26 on Apple silicon.
- For USB sending: [Calibre](https://calibre-ebook.com) installed in `/Applications`. Close Calibre's window before sending, because it holds the Kindle connection.
- For Apple translation: the language pair downloaded under System Settings → General → Language & Region → Translation Languages.
- For Claude translation: an API key from [console.anthropic.com](https://console.anthropic.com). The book's text is sent to Anthropic for translation.

## Install

Download `Book-Forge.zip` from the [latest release](../../releases/latest), unzip it and move **Book Forge** to Applications.

The app is not notarized, so the first time macOS will refuse to open it. Right-click the app and choose **Open**, or go to System Settings → Privacy & Security and click **Open Anyway**. Alternatively, remove the quarantine flag:

```bash
xattr -dr com.apple.quarantine "/Applications/Book Forge.app"
```

## Build from source

No Xcode project is needed, only the command-line tools:

```bash
./build.sh            # builds build/Book Forge.app
./build.sh --install  # also copies it to /Applications
```

## Command-line modes

The app binary also runs without a window, which is handy for testing:

```bash
BookForge --convert book.pdf book.epub [id|en|ja]
BookForge --translate in.epub out.epub id en [apple|claude]   # key from ANTHROPIC_API_KEY or the Keychain
BookForge --send book.epub                                    # USB upload to a connected Kindle
```

The binary is at `Book Forge.app/Contents/MacOS/BookForge`.

## Notes

Only convert and translate books you own, for your own reading.
