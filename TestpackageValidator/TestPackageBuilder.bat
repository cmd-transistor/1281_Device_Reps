@echo off
rem X81Z Test Package Builder - needs Python 3.12 with pandas, numpy, openpyxl, xlsxwriter and PySide6
cd /d "%~dp0"
python -c "import PySide6" 2>nul || python -m pip install --user PySide6
python testpackage_gui.py
if errorlevel 1 pause
