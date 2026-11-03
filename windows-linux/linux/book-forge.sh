#!/bin/sh
# Starts Book Forge with Calibre's Python. install.sh adds it to your applications menu.
here="$(cd "$(dirname "$0")" && pwd)"
exe="$(command -v calibre-debug || true)"
[ -z "$exe" ] && [ -x /opt/calibre/calibre-debug ] && exe=/opt/calibre/calibre-debug
if [ -z "$exe" ]; then
  msg="Book Forge needs Calibre. Install it from https://calibre-ebook.com/download_linux and try again."
  command -v notify-send >/dev/null && notify-send "Book Forge" "$msg"
  echo "$msg" >&2
  exit 1
fi
exec "$exe" -e "$here/run.py" "$@"
