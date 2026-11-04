@echo off
rem Adds Book Forge to the Start menu and the desktop. Run it again if you move this folder.
setlocal
if not exist "%~dp0Book Forge.exe" (
  echo Run this from the unzipped "Book Forge" folder, next to "Book Forge.exe".
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {" ^
  "  $s = $ws.CreateShortcut((Join-Path $dir 'Book Forge.lnk'));" ^
  "  $s.TargetPath = '%~dp0Book Forge.exe';" ^
  "  $s.WorkingDirectory = '%~dp0';" ^
  "  $s.Description = 'Book Forge';" ^
  "  $s.Save() }"
if errorlevel 1 (
  echo Couldn't create the shortcuts. You can still start Book Forge with "Book Forge.exe".
) else (
  echo Book Forge is in the Start menu and on your desktop.
)
pause
