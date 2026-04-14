# DroidRecord

A self-hosted VPS screen recorder with a web UI. Records your virtual display (Xvfb :1), manages saved recordings, and uploads them to Google Drive via rclone. Runs a full Android 13 emulator via Redroid (Android in Docker), visible through scrcpy on the virtual display.

---

## Stack

- **Backend:** Python 3 + Flask
- **Frontend:** Vanilla HTML/JS (no build step needed)
- **Recording:** ffmpeg + Xvfb (virtual display)
- **Window manager:** Openbox
- **Remote viewer:** noVNC + x11vnc
- **Uploads:** rclone → Google Drive
- **Android (optional):** Redroid (Android in Docker) + scrcpy

---

## Requirements

- Ubuntu 22.04 VPS (root access)
- Port 6080 open (web UI)
- Port 6081 open (noVNC viewer, optional)
- Kernel with `binder` support (standard Ubuntu 22.04 HWE kernel works)

---

## Quick Install

```bash
git clone <your-repo-url> DroidRecord
cd DroidRecord
sudo bash install.sh
```

The script installs and starts everything automatically, including systemd services, Docker, adb, scrcpy, and creates the Redroid container.

---

## Manual Setup (step by step)

### 1. Install system packages

```bash
sudo apt-get update -y
sudo apt-get install -y \
  xvfb openbox ffmpeg python3 python3-pip \
  novnc websockify x11vnc adb scrcpy
```

### 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo bash
sudo systemctl enable --now docker
```

### 3. Install rclone

```bash
curl https://rclone.org/install.sh | sudo bash
```

### 4. Set up Redroid (Android 13 in Docker)

Pull the image and create the container:

```bash
docker pull redroid/redroid:13.0.0-latest

sudo mkdir -p /data/redroid

docker run -itd \
  --name redroid \
  --privileged \
  -v /data/redroid:/data \
  -p 5555:5555 \
  redroid/redroid:13.0.0-latest \
  androidboot.redroid_gpu_mode=guest
```

`androidboot.redroid_gpu_mode=guest` uses software rendering — required on VPS without GPU passthrough.

The container persists across reboots. Use `docker start redroid` / `docker stop redroid` to control it, or use the **Start / Stop Emulator** buttons in the DroidRecord web UI.

**To display Android on the virtual screen** (what gets recorded):

```bash
docker start redroid
sleep 3
adb connect localhost:5555
DISPLAY=:1 scrcpy --serial localhost:5555 --no-audio
```

The DroidRecord UI does all of this automatically when you click **Start Emulator**.

### 5. Configure rclone for Google Drive

```bash
rclone config
```

When prompted:
- Name: **gdrive**
- Type: **drive** (Google Drive)
- Follow the OAuth flow in your browser

DroidRecord will upload recordings to a folder called `DroidRecord/` in your Drive root.

### 6. Start the virtual display

```bash
Xvfb :1 -screen 0 1280x720x24 &
DISPLAY=:1 openbox --sm-disable &
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

The Redroid container is managed by Docker, not systemd. To auto-start it on boot:

```bash
docker update --restart unless-stopped redroid
```

---

## Usage

### Web UI — http://\<ip\>:6080

**Android Emulator panel:**

| Button | Action |
|---|---|
| Start Emulator | `docker start redroid` → `adb connect` → launches `scrcpy` on the virtual display |
| Stop Emulator | Kills `scrcpy` → `docker stop redroid` |

Status pills show the Docker container state and whether scrcpy is running.

**Recording panel:**

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
| `REDROID_CONTAINER` | `redroid` | Docker container name |
| `REDROID_ADB_SERIAL` | `localhost:5555` | ADB address for scrcpy |

---

## Troubleshooting

**ffmpeg fails to start**
- Ensure Xvfb is running: `ps aux | grep Xvfb`
- Check the display env: `echo $DISPLAY` (should be `:1`)

**rclone upload fails**
- Run `rclone listremotes` — you should see `gdrive:`
- Test manually: `rclone lsd gdrive:`

**Redroid container fails to start**
- Check Docker is running: `systemctl status docker`
- Check the container exists: `docker ps -a | grep redroid`
- View container logs: `docker logs redroid`
- Recreate if needed: `docker rm redroid` then re-run the `docker run` command from the setup section

**scrcpy cannot connect**
- Ensure the container is running: `docker ps | grep redroid`
- Connect ADB manually: `adb connect localhost:5555`
- Verify ADB sees the device: `adb devices`
- Run scrcpy manually: `DISPLAY=:1 scrcpy --serial localhost:5555 --no-audio`
- Android may need a few seconds after container start before ADB is ready — wait 3–5 seconds then try again

**Android screen black / scrcpy connects but shows nothing**
- The container may still be booting — wait 10–15 seconds after `docker start`
- Check container logs: `docker logs redroid --tail 20`

**Port 6080 not reachable**
- Check firewall: `sudo ufw allow 6080/tcp`

---

## License

MIT
