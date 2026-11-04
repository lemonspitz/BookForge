# PyInstaller recipe: builds dist/Book Forge/ (Windows: "Book Forge.exe", Linux: "book-forge").
import os, sys
here = os.path.abspath(SPECPATH)
windows = sys.platform == 'win32'

a = Analysis(
    [os.path.join(here, 'run.py')],
    pathex=[here],
    datas=[(os.path.join(here, 'bookforge', 'reader.js'), 'bookforge'),
           (os.path.join(here, 'bookforge', 'icon.png'), 'bookforge'),
           (os.path.join(here, 'bookforge', 'icon.ico'), 'bookforge'),
           (os.path.join(here, '..', 'Resources', 'device.py'), '.'),
           (os.path.join(here, '..', 'Resources', 'languages.json'), 'bookforge'),
           (os.path.join(here, 'THIRD-PARTY.md'), '.')],
    hiddenimports=['bookforge.readers.wpd', 'bookforge.readers.libmtp', 'bookforge.readers.calibre_bridge',
                   'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets'] + (['comtypes.client', 'comtypes.stream'] if windows else []),
    excludes=['tkinter', 'PySide6.Qt3DCore', 'PySide6.QtQuick3D', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
              'PySide6.QtMultimedia', 'PySide6.QtBluetooth', 'PySide6.QtSensors', 'PySide6.QtLocation'],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Book Forge' if windows else 'book-forge',
          console=False, icon=os.path.join(here, 'bookforge', 'icon.ico' if windows else 'icon.png'))
coll = COLLECT(exe, a.binaries, a.datas, name='Book Forge')
