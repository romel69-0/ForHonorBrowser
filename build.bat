@echo off
rem Builds ForHonorBrowser.exe next to this file. Needs: pip install -r requirements.txt pyinstaller
cd /d "%~dp0"
python -m PyInstaller --noconfirm --onefile --console --name ForHonorBrowser --add-data "%~dp0index.html;." --distpath . --workpath build --specpath build server.py
