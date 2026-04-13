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

```bash
curl -s https://repo.waydro.id/waydroid.gpg | sudo gpg --dearmor \
  -o /usr/share/keyrings/waydroid.gpg
echo "deb [signed-by=/usr/share/keyrings/waydroid.gpg] \
  https://repo.waydro.id/ jammy main" | \
  sudo tee /etc/apt/sources.list.d/waydroid.list
sudo apt-get update && sudo apt-get install -y waydroid
sudo waydroid init -s GAPPS -f
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

```bash
x11vnc -display :1 -rfbport 5900 -nopw -listen localhost -xkb -forever &
/usr/share/novnc/utils/novnc_proxy --vnc localhost:5900 --listen 6081 &
```

Open **http://\<your-server-ip\>:6081/vnc.html** to see the virtual screen live.

---

## Systemd services (auto-start)

The `install.sh` script creates three systemd services:

| Service | Purpose |
|---|---|
| `xvfb-openbox` | Virtual display + window manager |
| `droidrecord` | Flask web UI on port 6080 |
| `novnc` | In-browser display viewer on port 6081 |

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
| Pause | Freeze recording (uses SIGSTOP) |
| Resume | Continue from where you paused |
| Stop | Finalize and save the MP4 file |

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

**Port 6080 not reachable**
- Check firewall: `sudo ufw allow 6080/tcp`

---

## License

MIT
