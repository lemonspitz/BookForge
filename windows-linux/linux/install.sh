#!/bin/sh
# Adds Book Forge to your applications menu. Run it again if you move this folder.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$apps"
cat > "$apps/book-forge.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Book Forge
Comment=E-book library, PDF to EPUB, translation and Kindle sync
Exec="$here/book-forge" %F
Icon=$here/_internal/bookforge/icon.png
Terminal=false
Categories=Office;Viewer;
MimeType=application/epub+zip;application/pdf;
StartupWMClass=book-forge
DESKTOP
command -v update-desktop-database >/dev/null && update-desktop-database "$apps" || true
echo "Book Forge is in your applications menu."
