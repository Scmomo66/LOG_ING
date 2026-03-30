@echo off
title Building Android Log Viewer

echo Installing PyInstaller...
py -3 -m pip install pyinstaller

echo.
echo Building exe...
py -3 -m PyInstaller --onefile --windowed --name "AndroidLogViewer" main_v12.py

echo.
echo Done! Check dist folder for AndroidLogViewer.exe
pause
