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
Exec="$here/book-forge.sh" %F
Icon=$here/bookforge/icon.png
Terminal=false
Categories=Office;Viewer;
MimeType=application/epub+zip;application/pdf;
StartupWMClass=book-forge
DESKTOP
command -v update-desktop-database >/dev/null && update-desktop-database "$apps" || true
if ! command -v calibre-debug >/dev/null && [ ! -x /opt/calibre/calibre-debug ]; then
  echo "Note: Book Forge needs Calibre: https://calibre-ebook.com/download_linux"
fi
echo "Book Forge is in your applications menu."
