@echo off
title Building Android Log Viewer

echo Installing dependencies...
py -3 -m pip install -r requirements.txt

echo.
echo Building exe...
py -3 -m PyInstaller --onefile --windowed --name "AndroidLogViewer" main.py

echo.
echo Done! Check dist folder for AndroidLogViewer.exe
pause
