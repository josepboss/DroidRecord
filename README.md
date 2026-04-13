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

**Step 2 — the lxc-start wrapper handles the LXC 5.0 fix automatically**

`install.sh` installs a shim over `/usr/bin/lxc-start` that fixes this transparently on every container start. No manual config patching is needed.

**Why the old config-patch approach does not work:**

LXC 5.0 resolves relative `lxc.mount.entry` target paths against `/usr/lib/x86_64-linux-gnu/lxc/` on the host instead of the container rootfs — causing `Failed to create directory "/usr/lib/x86_64-linux-gnu/lxc/dev"` errors. The affected files are both `config_nodes` **and** `config_session`. Critically, **Waydroid regenerates both files on every container start**, so any pre-patch is immediately overwritten. Patching after the fact never runs at the right moment.

**The wrapper approach:**

`install.sh` backs up the real `lxc-start` to `lxc-start.real` and replaces it with a bash shim. The shim is the only point in the call chain where Waydroid has already written the configs but LXC hasn't read them yet:

```
Waydroid writes config_nodes + config_session
        ↓
/usr/bin/lxc-start (shim) — patches both files, then:
        ↓
/usr/bin/lxc-start.real — reads the now-correct config
```

The shim uses Python to rewrite every `lxc.mount.entry` line whose target is a relative path, prefixing it with `/var/lib/waydroid/rootfs`. It covers all fstypes (tmpfs, bind, ext4), both config files, and runs on every restart automatically.

If `install.sh` was not run and you need to install the shim manually:

```bash
sudo mv /usr/bin/lxc-start /usr/bin/lxc-start.real

sudo tee /usr/bin/lxc-start <<'EOF'
#!/bin/bash
ROOTFS=/var/lib/waydroid/rootfs
LXC_DIR=/var/lib/waydroid/lxc/waydroid
patch_config() {
  local cfg="$1"; [ -f "$cfg" ] || return
  python3 - "$cfg" "$ROOTFS" <<'PYEOF'
import sys
cfg_path, rootfs = sys.argv[1], sys.argv[2]
lines_out = []
with open(cfg_path) as f:
    for line in f:
        if line.startswith('lxc.mount.entry'):
            fields = line.split()
            if len(fields) >= 4 and fields[2] == 'tmpfs':
                continue  # drop tmpfs entries entirely — LXC 5.0 can't handle them
            if len(fields) >= 4 and not fields[3].startswith('/'):
                fields[3] = rootfs + '/' + fields[3]
                line = ' '.join(fields) + '\n'
        lines_out.append(line)
with open(cfg_path, 'w') as f:
    f.writelines(lines_out)
PYEOF
}
patch_config "$LXC_DIR/config_nodes"
patch_config "$LXC_DIR/config_session"
exec /usr/bin/lxc-start.real "$@"
EOF

sudo chmod +x /usr/bin/lxc-start
```

**Step 3 — pre-start requirements and display:**

Two host-side resources must exist before `lxc-start` runs — the DroidRecord UI and `install.sh` both handle these automatically, but if you're starting manually:

```bash
# PulseAudio socket directory (source for bind mount in config_session)
mkdir -p /run/user/0/pulse

# waydroid0 network bridge (required for container networking)
ip link show waydroid0 2>/dev/null || (
  ip link add waydroid0 type bridge &&
  ip addr add 192.168.250.1/24 dev waydroid0 &&
  ip link set waydroid0 up
)

# Now start the container and show the UI
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

**Waydroid container fails — `Failed to create directory "/usr/lib/x86_64-linux-gnu/lxc/..."` or `Failed to mount "tmpfs"` (LXC 5.0 bug)**

LXC 5.0 resolves relative `lxc.mount.entry` target paths against the host LXC library dir instead of the container rootfs. Waydroid regenerates both `config_nodes` and `config_session` on every container start, so patching the files directly never survives a restart.

The correct fix is the **lxc-start wrapper** installed by `install.sh`. Check whether it is in place:

```bash
head -1 /usr/bin/lxc-start
# Should print: #!/bin/bash
# If it prints ELF or nothing, the wrapper is not installed.
```

If the wrapper is missing (e.g. on a system that didn't use `install.sh`), install it manually — see the Waydroid section of this README for the one-paste install command.

If the wrapper is present but the container still fails, check the patched config right after a failed start:

```bash
grep 'lxc.mount.entry' /var/lib/waydroid/lxc/waydroid/config_nodes | head -5
# All targets should start with /var/lib/waydroid/rootfs/...
```

If targets are still relative, the wrapper may have failed silently — check that `python3` is available at `/usr/bin/python3` and that the wrapper is executable (`ls -la /usr/bin/lxc-start`).

**Waydroid: data/pulse bind mount source missing or waydroid0 bridge missing**

Even after the wrapper fixes the target paths to absolute, the *source* paths for bind mounts must exist on the host before `lxc-start` runs. Two required resources:

```bash
# PulseAudio socket directory — must exist (socket itself is optional)
ls /run/user/0/pulse || mkdir -p /run/user/0/pulse

# waydroid0 bridge — required for container networking
ip link show waydroid0 || (
  ip link add waydroid0 type bridge &&
  ip addr add 192.168.250.1/24 dev waydroid0 &&
  ip link set waydroid0 up
)
```

The DroidRecord UI (Start Emulator button) and `install.sh` both create these automatically. If you bypassed both, create them manually before `systemctl start waydroid-container`.

**Port 6080 not reachable**
- Check firewall: `sudo ufw allow 6080/tcp`

---

## License

MIT
