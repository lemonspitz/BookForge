@echo off
rem Adds Book Forge to the Start menu and the desktop. Run it again if you move this folder.
setlocal
set "CD_EXE="
for %%P in ("%ProgramFiles%\Calibre2\calibre-debug.exe" "%ProgramFiles(x86)%\Calibre2\calibre-debug.exe") do if exist %%P set "CD_EXE=%%~P"
if not defined CD_EXE for /f "delims=" %%P in ('where calibre-debug.exe 2^>nul') do set "CD_EXE=%%P"
if not defined CD_EXE (
  echo Book Forge needs Calibre. Install it from https://calibre-ebook.com/download_windows first, then run Install.cmd again.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {" ^
  "  $s = $ws.CreateShortcut((Join-Path $dir 'Book Forge.lnk'));" ^
  "  $s.TargetPath = '%CD_EXE%';" ^
  "  $s.Arguments = '-e \"%~dp0run.py\"';" ^
  "  $s.WorkingDirectory = '%~dp0';" ^
  "  $s.IconLocation = '%~dp0bookforge\icon.ico';" ^
  "  $s.WindowStyle = 7;" ^
  "  $s.Description = 'Book Forge';" ^
  "  $s.Save() }"
if errorlevel 1 (
  echo Couldn't create the shortcuts. You can still start Book Forge with "Book Forge.cmd".
) else (
  echo Book Forge is in the Start menu and on your desktop.
)
pause
