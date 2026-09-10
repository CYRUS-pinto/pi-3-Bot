#!/bin/bash
# Kills any running TARS process — does NOT auto-start main.py
# Run python3 main.py yourself after this
pkill -f "python3.*main.py" || true
sleep 1
echo "[launch.sh] Old TARS process killed. Run manually: python3 main.py"
