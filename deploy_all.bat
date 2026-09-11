@echo off
REM TARS one-shot ship: packs tracked runtime files, one upload, unpack on Pi.
REM Usage: turn the Pi on, wait for WiFi, then run this. ~2 password prompts.
setlocal
set PI=cyrus@10.70.4.81
cd /d "%~dp0"
echo [1/3] Packing...
tar -czf %TEMP%\tars_ship.tgz --exclude=__pycache__ --exclude=*.log --exclude=*.pyc --exclude=previews --exclude=.git --exclude=backup_checkpoint* *.py *.json *.sh *.ps1 *.bat *.md requirements.txt .gitignore models arduino 2>nul
echo [2/3] Uploading (password prompt 1)...
scp -o ConnectTimeout=10 -o StrictHostKeyChecking=no %TEMP%\tars_ship.tgz %PI%:/tmp/tars_ship.tgz
if errorlevel 1 ( echo UPLOAD FAILED - is the Pi on WiFi? & exit /b 1 )
echo [3/3] Unpacking + verifying on Pi (password prompt 2)...
ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no %PI% "tar -xzf /tmp/tars_ship.tgz -C ~/TARS && rm -f /tmp/tars_ship.tgz && cd ~/TARS && python3 -m py_compile main.py config.py vision.py bridge.py && python3 test_demo_defaults.py"
del %TEMP%\tars_ship.tgz
echo DONE.
