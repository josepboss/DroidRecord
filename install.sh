#!/usr/bin/env bash
# DroidRecord install script — Ubuntu 22.04
# Run as root or with sudo
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

if [ "$(id -u)" -ne 0 ]; then
  error "Please run this script as root or with sudo."
  exit 1
fi

UBUNTU_VERSION=$(lsb_release -rs 2>/dev/null || echo "unknown")
if [[ "$UBUNTU_VERSION" != "22.04" ]]; then
  warn "This script is tested on Ubuntu 22.04. Detected: $UBUNTU_VERSION. Proceeding anyway..."
fi

info "Updating package lists..."
apt-get update -y

info "Installing base dependencies..."
apt-get install -y \
  curl wget gnupg2 ca-certificates lsb-release software-properties-common \
  build-essential git unzip sudo

info "Installing Xvfb (virtual framebuffer)..."
apt-get install -y xvfb

info "Installing Openbox (window manager)..."
apt-get install -y openbox

info "Installing ffmpeg..."
apt-get install -y ffmpeg

info "Installing Python 3 + pip..."
apt-get install -y python3 python3-pip python3-venv

info "Installing noVNC + websockify..."
apt-get install -y novnc websockify x11vnc

info "Installing rclone..."
if ! command -v rclone &>/dev/null; then
  curl https://rclone.org/install.sh | bash
else
  info "rclone already installed, skipping."
fi

info "Installing Waydroid dependencies..."
apt-get install -y \
  curl python3-pip lzip wget \
  linux-headers-generic

info "Loading binder kernel module (required for Waydroid)..."
modprobe binder_linux devices="binder,hwbinder,vndbinder" || warn "binder_linux module not available — Waydroid will not work without it."
echo 'binder_linux' >> /etc/modules-load.d/waydroid.conf

info "Adding Waydroid repository..."
curl -s https://repo.waydro.id/waydroid.gpg | gpg --dearmor -o /usr/share/keyrings/waydroid.gpg
echo "deb [signed-by=/usr/share/keyrings/waydroid.gpg] https://repo.waydro.id/ jammy main" \
  > /etc/apt/sources.list.d/waydroid.list
apt-get update -y
apt-get install -y waydroid

warn "Waydroid installed but NOT initialised — init requires an active display session."
warn "After install, run manually over SSH:"
warn "  waydroid init -s GAPPS -f"
warn "  systemctl start waydroid-container"
warn "  DISPLAY=:1 waydroid show-full-ui"

info "Installing Python requirements for DroidRecord..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pip3 install -r "$SCRIPT_DIR/requirements.txt"

info "Creating /recordings directory..."
mkdir -p /recordings
chmod 777 /recordings

info "Creating systemd service for Xvfb + Openbox..."
cat > /etc/systemd/system/xvfb-openbox.service <<'SYSTEMD'
[Unit]
Description=Xvfb virtual display + Openbox
After=network.target

[Service]
Type=forking
ExecStart=/bin/bash -c 'Xvfb :1 -screen 0 1280x720x24 &disown; sleep 1; DISPLAY=:1 openbox --sm-disable &disown'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SYSTEMD

info "Creating systemd service for DroidRecord (Flask)..."
cat > /etc/systemd/system/droidrecord.service <<SYSTEMD
[Unit]
Description=DroidRecord Flask App
After=xvfb-openbox.service

[Service]
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/app.py
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:1

[Install]
WantedBy=multi-user.target
SYSTEMD

info "Creating systemd service for x11vnc..."
cat > /etc/systemd/system/x11vnc.service <<'SYSTEMD'
[Unit]
Description=x11vnc VNC Server
After=xvfb-openbox.service
Requires=xvfb-openbox.service

[Service]
ExecStart=/usr/bin/x11vnc -display :1 -rfbport 5900 -nopw -listen localhost -xkb -forever -shared
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
SYSTEMD

info "Creating systemd service for noVNC..."
cat > /etc/systemd/system/novnc.service <<'SYSTEMD'
[Unit]
Description=noVNC Web Client
After=x11vnc.service
Requires=x11vnc.service

[Service]
ExecStart=/usr/share/novnc/utils/novnc_proxy --vnc localhost:5900 --listen 6081
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload
systemctl enable xvfb-openbox x11vnc droidrecord novnc
systemctl start  xvfb-openbox

info "Waiting 2s for Xvfb to start..."
sleep 2

systemctl start x11vnc droidrecord novnc

info "Opening firewall ports..."
if command -v ufw &>/dev/null; then
  ufw allow 6080/tcp comment "DroidRecord UI"
  ufw allow 6081/tcp comment "noVNC"
  info "ufw: ports 6080 and 6081 opened."
else
  warn "ufw not found — open ports 6080 and 6081 manually in your firewall/security group."
fi

info ""
info "=============================================="
info "  DroidRecord installation complete!"
info "=============================================="
info "  Web UI (recorder):  http://<YOUR-IP>:6080"
info "  noVNC (display):    http://<YOUR-IP>:6081/vnc.html"
info ""
info "  Next steps:"
info "  1. Configure rclone for Google Drive:"
info "     rclone config   (name the remote: gdrive)"
info "  2. Initialise Waydroid manually over SSH:"
info "     waydroid init -s GAPPS -f"
info "     systemctl start waydroid-container"
info "     DISPLAY=:1 waydroid show-full-ui"
info "  3. Open the recorder UI and start recording."
info "=============================================="
