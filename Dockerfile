# Use the official Playwright Python image so Chromium and all browser system
# dependencies are already installed and version-locked to match requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

# ---------------------------------------------------------------------------
# System packages
#   xvfb        – virtual framebuffer; required by tkinter, pyautogui, and
#                 any headed Playwright usage inside a headless container
#   python3-tk  – Tcl/Tk runtime that customtkinter is built on
#   tk-dev      – header files needed when Python compiles the _tkinter ext
#   libxtst6    – X Test Extension; required by pyautogui for input synthesis
#   libxi6      – X Input Extension; same reason
#   scrot       – screenshot utility used by pyautogui on Linux
#   xdotool     – keyboard/mouse injection fallback for pyautogui
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    python3-tk \
    tk-dev \
    libxtst6 \
    libxi6 \
    scrot \
    xdotool \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies before copying source so this layer is cached
# as long as requirements.txt is unchanged.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chrome for Testing so channel="chrome" resolves to Playwright's own
# Chrome binary rather than a system Chrome that isn't present in this image.
# This preserves the headed Chrome behaviour (cookie banner visibility, etc.)
# that bundled Chromium does not reproduce.
RUN python -m playwright install chrome

# Copy application source.
# credentials.enc and secret.key are excluded via .dockerignore and must be
# mounted as read-only volumes at runtime (see docker-compose.yml).
COPY . .

# Ensure the logs directory exists so the volume mount and log file path both work.
RUN mkdir -p /app/logs

# Strip Windows line-endings in case the file was committed with CRLF, then
# make the script executable.
COPY docker-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh

# Display number that Xvfb will listen on — consumed by tkinter, pyautogui,
# and the Playwright browser launched by the app.
ENV DISPLAY=:99

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# cli.py is the headless entry point used in Docker / cloud.
# To run the GUI instead (e.g. with VNC): override CMD at runtime.
CMD ["python", "cli.py"]
