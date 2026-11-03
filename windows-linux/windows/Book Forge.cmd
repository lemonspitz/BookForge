@echo off
rem Starts Book Forge with Calibre's Python. Install.cmd puts shortcuts in the Start menu and on the desktop.
setlocal
set "CD_EXE="
for %%P in ("%ProgramFiles%\Calibre2\calibre-debug.exe" "%ProgramFiles(x86)%\Calibre2\calibre-debug.exe") do if exist %%P set "CD_EXE=%%~P"
if not defined CD_EXE for /f "delims=" %%P in ('where calibre-debug.exe 2^>nul') do set "CD_EXE=%%P"
if not defined CD_EXE (
  echo Book Forge needs Calibre. Install it from https://calibre-ebook.com/download_windows and try again.
  pause
  exit /b 1
)
start "" /min "%CD_EXE%" -e "%~dp0run.py" %*
