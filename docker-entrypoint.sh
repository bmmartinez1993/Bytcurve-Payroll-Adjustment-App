#!/bin/bash
set -e

# The container starts as root so this step can fix ownership of ./logs,
# which is bind-mounted from the host and may not already belong to pwuser
# (the host-side UID is whatever created the directory, not the image's).
# Once that's done, re-exec this same script as pwuser via setpriv so Xvfb,
# Chrome, and the Python process itself never run as root. setpriv replaces
# the current process (like exec), so pwuser ends up as PID 1 and still
# receives SIGTERM/SIGINT correctly.
if [ "$(id -u)" = "0" ]; then
    chown -R pwuser:pwuser /app/logs 2>/dev/null || true
    exec setpriv --reuid=pwuser --regid=pwuser --init-groups "$0" "$@"
fi

# setpriv does not clear the inherited environment, so HOME is still "/root"
# from the root phase above unless we force it — a plain ${HOME:-...}
# fallback would never trigger since HOME is already set (just to the wrong
# value), not unset.
export HOME=/home/pwuser

# Start a virtual framebuffer on display :99.
# Resolution 1920x1080 with 24-bit color gives the app enough canvas.
# -ac disables access control so any process can connect. This now only
# matters within pwuser's own processes (Xvfb runs after the setpriv drop
# above), not root — the same setup that made -ac necessary (python-xlib
# unconditionally reading ~/.Xauthority, see below) still applies per-user.
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
