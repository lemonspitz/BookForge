@echo off
rem Builds Book Forge for Windows into dist\Book-Forge-Windows.zip. Needs Python 3.11 or later from python.org.
setlocal
cd /d "%~dp0"
if not exist .venv-win py -3 -m venv .venv-win || python -m venv .venv-win
call .venv-win\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
rem comtypes writes its Windows Portable Devices bindings at first use; make them now so the app has them
python -c "import comtypes.client as c; c.GetModule('portabledeviceapi.dll'); c.GetModule('portabledevicetypes.dll')"
pyinstaller --noconfirm --clean bookforge.spec
copy /y windows\Install.cmd "dist\Book Forge\Install.cmd"
copy /y windows\INSTALL.txt "dist\Book Forge\INSTALL.txt"
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\Book Forge' -DestinationPath 'dist\Book-Forge-Windows.zip'"
echo Built dist\Book-Forge-Windows.zip
