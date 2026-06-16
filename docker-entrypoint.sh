#!/bin/bash
set -e

# Start a virtual framebuffer on display :99.
# Resolution 1920x1080 with 24-bit color gives the app enough canvas.
# -ac disables access control so any process can connect.
# +render enables the RENDER extension needed by modern GTK/Tk themes.
# -noreset keeps Xvfb alive even after the last client disconnects.
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait for the display socket to appear before handing off to the app.
for i in $(seq 1 10); do
    [ -S /tmp/.X11-unix/X99 ] && break
    sleep 0.5
done

# python-xlib (pulled in by pyautogui -> mouseinfo) opens the display at import
# time and unconditionally reads ~/.Xauthority, raising XauthError if the file
# is missing — even though Xvfb runs with -ac (access control disabled).
# Create an empty authority file so Xlib finds zero entries and connects
# without auth via -ac.
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
touch "$XAUTHORITY"

exec "$@"
