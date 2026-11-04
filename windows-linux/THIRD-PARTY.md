# Software included in Book Forge

Book Forge itself is MIT-licensed. The Windows and Linux apps bundle:

| Component | License | Source |
|---|---|---|
| Qt 6 and PySide6 (Qt for Python) | LGPL-3.0 | https://download.qt.io/official_releases/QtForPython/ |
| Qt WebEngine (Chromium) | LGPL-3.0 and Chromium's licenses | https://code.qt.io/cgit/qt/qtwebengine.git |
| Python | PSF License | https://www.python.org |
| pypdfium2 and PDFium | Apache-2.0 / BSD-3-Clause | https://github.com/pypdfium2-team/pypdfium2 |
| lxml (libxml2, libxslt) | BSD-3-Clause / MIT | https://lxml.de |
| regex | Apache-2.0 | https://github.com/mrabarnett/mrab-regex |
| Pillow | MIT-CMU (HPND) | https://python-pillow.org |
| Send2Trash | BSD-3-Clause | https://github.com/arsenetar/send2trash |
| comtypes (Windows) | MIT | https://github.com/enthought/comtypes |

The Qt libraries are loaded as separate shared libraries; you may replace them with your own builds of the same
version. On Linux, MTP readers are reached through your system's libmtp (LGPL-2.1), which isn't bundled.

The Mac app bundles libmtp 1.1.22, libusb 1.0 and GNU libiconv (all LGPL-2.1), taken from Calibre's macOS build:
sources at https://github.com/libmtp/libmtp, https://github.com/libusb/libusb and https://www.gnu.org/software/libiconv/
(Calibre's build recipes: https://github.com/kovidgoyal/build-calibre).

Calibre (GPL-3.0) is not included. When it's installed, Book Forge runs its ebook-convert program to turn EPUBs
into Kindle books and Kindle books into EPUBs.
