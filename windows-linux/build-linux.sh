#!/bin/sh
# Builds Book Forge for Linux into dist/Book-Forge-Linux.tar.gz. Needs Python 3.11+ with venv (e.g. apt install python3-venv).
set -e
cd "$(dirname "$0")"
[ -d .venv-linux ] || python3 -m venv .venv-linux
. .venv-linux/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --clean bookforge.spec
cp linux/install.sh linux/INSTALL.txt "dist/Book Forge/"
chmod +x "dist/Book Forge/install.sh"
tar -C dist -czf dist/Book-Forge-Linux.tar.gz "Book Forge"
echo "Built dist/Book-Forge-Linux.tar.gz"
