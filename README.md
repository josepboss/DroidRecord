# DroidRecord

A self-hosted VPS screen recorder with a web UI. Records your virtual display (Xvfb :1), manages saved recordings, and uploads them to Google Drive via rclone.

---

## Stack

- **Backend:** Python 3 + Flask
- **Frontend:** Vanilla HTML/JS (no build step needed)
- **Recording:** ffmpeg + Xvfb (virtual display)
- **Window manager:** Openbox
- **Remote viewer:** noVNC + x11vnc
- **Uploads:** rclone → Google Drive
- **Android (optional):** Waydroid + GApps

---

## Requirements

- Ubuntu 22.04 VPS (root access)
- Port 6080 open (web UI)
- Port 6081 open (noVNC viewer, optional)

---

## Quick Install

```bash
git clone <your-repo-url> DroidRecord
cd DroidRecord
sudo bash install.sh
```

The script installs and starts everything automatically, including systemd services.

---

## Manual Setup (step by step)

### 1. Install system packages

```bash
sudo apt-get update -y
sudo apt-get install -y \
  xvfb openbox ffmpeg python3 python3-pip \
  novnc websockify x11vnc
```

### 2. Install rclone

```bash
curl https://rclone.org/install.sh | sudo bash
```

### 3. Install Waydroid + GApps (optional)

Install the package:

```bash
# Load binder kernel module (required — Waydroid will not start without it)
sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"
echo 'binder_linux' | sudo tee /etc/modules-load.d/waydroid.conf

curl -s https://repo.waydro.id/waydroid.gpg | sudo gpg --dearmor \
  -o /usr/share/keyrings/waydroid.gpg
echo "deb [signed-by=/usr/share/keyrings/waydroid.gpg] \
  https://repo.waydro.id/ jammy main" | \
  sudo tee /etc/apt/sources.list.d/waydroid.list
sudo apt-get update && sudo apt-get install -y waydroid
```

> **Do not run `waydroid init` from the install script or a non-interactive session.** It downloads system images and requires a working display context. Run the steps below manually over SSH after the system is up:

**Step 1 — initialise images:**

```bash
waydroid init -s GAPPS -f
```

**Step 2 — patch the LXC config (required on LXC 5.0 / Ubuntu 22.04 + kernel 6.x):**

LXC 5.0 changed how it resolves relative `lxc.mount.entry` target paths. Waydroid's generated `config_nodes` uses bare relative paths (e.g. `dev`, `tmp`) which LXC 5.0 now resolves against the host LXC library dir (`/usr/lib/x86_64-linux-gnu/lxc/`) instead of the container rootfs, causing mount failures like:

```
Failed to mount "tmpfs" on "/usr/lib/x86_64-linux-gnu/lxc/dev"
```

Fix: rewrite the paths to absolute before starting the container:

```bash
ROOTFS=/var/lib/waydroid/rootfs
CONFIG=/var/lib/waydroid/lxc/waydroid/config_nodes

# Prefix all relative tmpfs mount targets with the container rootfs path
sed -i -E \
  "s|^(lxc\.mount\.entry = tmpfs) ([^ ]+) (tmpfs.*)|\1 ${ROOTFS}/\2 \3|g" \
  "$CONFIG"
```

This turns entries like:
```
lxc.mount.entry = tmpfs dev tmpfs nosuid 0 0
```
into:
```
lxc.mount.entry = tmpfs /var/lib/waydroid/rootfs/dev tmpfs nosuid 0 0
```

Run `grep 'lxc.mount.entry = tmpfs' "$CONFIG"` to verify all entries now use absolute paths before continuing.

**Step 3 — start and display:**

```bash
systemctl start waydroid-container
DISPLAY=:1 waydroid show-full-ui
```

### 4. Configure rclone for Google Drive

```bash
rclone config
```

When prompted:
- Name: **gdrive**
- Type: **drive** (Google Drive)
- Follow the OAuth flow in your browser

DroidRecord will upload recordings to a folder called `DroidRecord/` in your Drive root.

### 5. Start the virtual display

```bash
Xvfb :1 -screen 0 1280x720x24 &
DISPLAY=:1 openbox --sm-disable &
```

### 6. Start Waydroid session (optional)

```bash
waydroid session start &
DISPLAY=:1 waydroid show-full-ui &
```

### 7. Install Python dependencies

```bash
pip3 install -r requirements.txt
```

### 8. Create the recordings folder

```bash
sudo mkdir -p /recordings
sudo chmod 777 /recordings
```

### 9. Start DroidRecord

```bash
DISPLAY=:1 python3 app.py
```

Then open **http://\<your-server-ip\>:6080** in your browser.

---

## (Optional) noVNC — view the virtual display in browser

x11vnc and noVNC run as **separate** systemd services (installed by `install.sh`). To start them manually:

```bash
# Start x11vnc first (VNC server for the virtual display)
x11vnc -display :1 -rfbport 5900 -nopw -listen localhost -xkb -forever -shared &

# Then start noVNC (web proxy to the VNC server)
/usr/share/novnc/utils/novnc_proxy --vnc localhost:5900 --listen 6081 &
```

Open **http://\<your-server-ip\>:6081/vnc.html** to see the virtual screen live.

---

## Systemd services (auto-start)

The `install.sh` script creates four systemd services:

| Service | Purpose |
|---|---|
| `xvfb-openbox` | Virtual display + window manager |
| `x11vnc` | VNC server for the virtual display (port 5900) |
| `novnc` | In-browser noVNC proxy (port 6081) — depends on x11vnc |
| `droidrecord` | Flask web UI on port 6080 |

Manage them with:

```bash
sudo systemctl status droidrecord
sudo systemctl restart droidrecord
sudo journalctl -u droidrecord -f
```

---

## Usage

### Web UI — http://\<ip\>:6080

| Button | Action |
|---|---|
| Start | Begin recording the virtual display |
| Pause | Freeze recording (uses SIGSTOP on ffmpeg) |
| Resume | Continue from where you paused |
| Stop | Finalize and save the MP4 file |

> **Pause note:** Pause uses `SIGSTOP` to freeze the ffmpeg process. The MP4 file stays open and incomplete while paused. This is safe for short pauses (under a few minutes), but extended pauses risk file corruption on some kernels or filesystems. When in doubt, stop the recording and start a new one.

Recordings are saved to `/recordings/recording_YYYYMMDD_HHMMSS.mp4`.

### Recordings list

- **Upload** — Sends the file to Google Drive (`gdrive:DroidRecord/`) via rclone, then deletes the local copy.
- **Delete** — Permanently removes the local file.

---

## Configuration

Edit `app.py` to change:

| Variable | Default | Description |
|---|---|---|
| `DISPLAY` | `:1` | X display to capture |
| `RESOLUTION` | `1280x720` | Recording resolution |
| `FRAMERATE` | `30` | Frames per second |
| `RECORDINGS_DIR` | `/recordings` | Where files are saved |

---

## Troubleshooting

**ffmpeg fails to start**
- Ensure Xvfb is running: `ps aux | grep Xvfb`
- Check the display env: `echo $DISPLAY` (should be `:1`)

**rclone upload fails**
- Run `rclone listremotes` — you should see `gdrive:`
- Test manually: `rclone lsd gdrive:`

**Waydroid not showing**
- Start the session: `waydroid session start`
- Show the UI: `DISPLAY=:1 waydroid show-full-ui`
- Check kernel modules: `lsmod | grep binder`

**Waydroid container fails to start — `Failed to mount "tmpfs" on "/usr/lib/x86_64-linux-gnu/lxc/..."` (LXC 5.0 bug)**

This is a known incompatibility between Waydroid 1.6.x and LXC 5.0.0 on Ubuntu 22.04 with kernel 6.x. LXC 5.0 resolves relative `lxc.mount.entry` target paths against the host LXC library directory instead of the container rootfs.

Patch the generated config after `waydroid init`:

```bash
ROOTFS=/var/lib/waydroid/rootfs
CONFIG=/var/lib/waydroid/lxc/waydroid/config_nodes

sed -i -E \
  "s|^(lxc\.mount\.entry = tmpfs) ([^ ]+) (tmpfs.*)|\1 ${ROOTFS}/\2 \3|g" \
  "$CONFIG"

# Verify — all tmpfs targets should now be absolute paths:
grep 'lxc.mount.entry = tmpfs' "$CONFIG"

systemctl restart waydroid-container
```

Affected mount points: `dev`, `mnt/extra`, `tmp`, `var`, `run`. The patch makes each target absolute (e.g. `/var/lib/waydroid/rootfs/dev`) so LXC 5.0 resolves it correctly inside the container.

**Port 6080 not reachable**
- Check firewall: `sudo ufw allow 6080/tcp`

---

## License

MIT
