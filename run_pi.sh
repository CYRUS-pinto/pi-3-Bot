#!/bin/bash
set -e
cd "$(dirname "$0")"

# Uncomment these two lines only when using a console/framebuffer SDL setup:
# export SDL_VIDEODRIVER=fbcon
# export SDL_FBDEV=/dev/fb0

exec python3 main.py
