#!/bin/zsh
# Builds "Book Forge.app" into ./build. Pass --install to copy it to /Applications.
set -euo pipefail
cd "$(dirname "$0")"

APP="build/Book Forge.app"
rm -rf build && mkdir -p build/obj "$APP/Contents/MacOS" "$APP/Contents/Resources"

swiftc -O -swift-version 5 -target arm64-apple-macos26.0 -import-objc-header Sources/MTP.h \
  -framework AppKit -framework SwiftUI -framework PDFKit -framework Vision -framework WebKit -framework IOKit \
  Sources/*.swift -o build/obj/BookForge 2>&1 | grep -v ' warning: ' || true
test -x build/obj/BookForge
cp build/obj/BookForge "$APP/Contents/MacOS/BookForge"
# libmtp and libusb (LGPL, from Calibre's build) reach MTP readers such as newer Kindles
mkdir -p "$APP/Contents/Frameworks"
cp vendor/mac/*.dylib "$APP/Contents/Frameworks/"

ICONSET=build/obj/AppIcon.iconset
mkdir -p "$ICONSET"
build/obj/BookForge --icon build/obj
for s in 16 32 128 256 512; do
  cp "build/obj/icon_$s.png" "$ICONSET/icon_${s}x${s}.png"
  cp "build/obj/icon_$((s * 2)).png" "$ICONSET/icon_${s}x${s}@2x.png"
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>BookForge</string>
  <key>CFBundleIdentifier</key><string>com.widagdos.bookforge</string>
  <key>CFBundleName</key><string>Book Forge</string>
  <key>CFBundleDisplayName</key><string>Book Forge</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.4</string>
  <key>CFBundleVersion</key><string>6</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppleEventsUsageDescription</key><string>Book Forge uses Mail to email books to your Kindle over Wi-Fi.</string>
  <key>CFBundleDocumentTypes</key>
  <array>
    <dict>
      <key>CFBundleTypeName</key><string>PDF or EPUB</string>
      <key>CFBundleTypeRole</key><string>Viewer</string>
      <key>LSHandlerRank</key><string>Alternate</string>
      <key>LSItemContentTypes</key><array><string>com.adobe.pdf</string><string>org.idpf.epub-container</string></array>
    </dict>
  </array>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP" >/dev/null
echo "Built $APP"

if [[ "${1:-}" == "--install" ]]; then
  rm -rf "/Applications/Book Forge.app"
  cp -R "$APP" /Applications/
  echo "Installed to /Applications/Book Forge.app"
fi
