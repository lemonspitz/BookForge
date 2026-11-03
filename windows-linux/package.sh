#!/bin/sh
# Builds dist/<version>/Book-Forge-Windows.zip and Book-Forge-Linux.zip from this folder and ../Resources/device.py.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
version="$(sed -n "s/^VERSION = '\(.*\)'/\1/p" "$here/bookforge/__init__.py")"
out="$here/../dist/$version"
mkdir -p "$out"
for platform in Windows Linux; do
  stage="$(mktemp -d)/Book Forge"
  mkdir -p "$stage/bookforge"
  cp "$here/run.py" "$stage/"
  cp "$here"/bookforge/*.py "$here"/bookforge/reader.js "$here"/bookforge/icon.png "$here"/bookforge/icon.ico "$stage/bookforge/"
  cp "$here/../Resources/device.py" "$stage/bookforge/device.py"
  if [ "$platform" = Windows ]; then
    for f in "$here"/windows/*; do
      # Windows wants CRLF line endings in .cmd and .txt files
      sed 's/$/\r/' "$f" > "$stage/$(basename "$f")"
    done
  else
    cp "$here"/linux/* "$stage/"
    chmod +x "$stage"/*.sh
  fi
  zip="$out/Book-Forge-$platform.zip"
  rm -f "$zip"
  (cd "$(dirname "$stage")" && zip -qrX "$zip" "Book Forge")
  rm -rf "$(dirname "$stage")"
  echo "$zip"
done
