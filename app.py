import os
import signal
import subprocess
import time
import glob
import threading
from datetime import datetime
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

RECORDINGS_DIR = "/recordings"
DISPLAY = ":1"
RESOLUTION = "1280x720"
FRAMERATE = "30"

WAYDROID_LXC_DIR    = "/var/lib/waydroid/lxc/waydroid"
WAYDROID_ROOTFS     = "/var/lib/waydroid/rootfs"
LXC_START_REAL      = "/usr/bin/lxc-start.real"
LXC_START_WRAPPER   = "/usr/bin/lxc-start"

# Wrapper script installed over /usr/bin/lxc-start.
# Waydroid regenerates config_nodes + config_session every container start then
# calls lxc-start.  This wrapper intercepts that call — the only moment where
# the configs exist but LXC hasn't read them yet — patches all relative mount
# entry targets to absolute paths inside the container rootfs, then execs the
# real lxc-start.  Works for both tmpfs and bind mounts; survives Waydroid
# restarts and upgrades because it runs at the OS syscall boundary.
LXC_WRAPPER_SCRIPT = r"""#!/bin/bash
# DroidRecord — LXC 5.0 / Waydroid 1.6.x mount-path shim
# Intercepts every lxc-start call and rewrites relative mount targets to
# absolute paths inside the container rootfs before LXC reads the configs.

ROOTFS=/var/lib/waydroid/rootfs
LXC_DIR=/var/lib/waydroid/lxc/waydroid

patch_config() {
  local cfg="$1"
  [ -f "$cfg" ] || return

  # Patch ALL lxc.mount.entry lines whose TARGET (4th whitespace-separated
  # field: key = source TARGET ...) is a relative path.
  # Uses field-split in Python rather than a regex to avoid ambiguity with
  # bind mounts that have an absolute source (/root/.local/share/...) and a
  # short relative target (data, run/user/0/pulse/native, waydroid0, etc.).
  python3 - "$cfg" "$ROOTFS" <<'PYEOF'
import sys

cfg_path, rootfs = sys.argv[1], sys.argv[2]

lines_out = []
with open(cfg_path) as f:
    for line in f:
        if line.startswith('lxc.mount.entry'):
            # fields: ['lxc.mount.entry', '=', 'SOURCE', 'TARGET', ...]
            fields = line.split()
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
"""

os.makedirs(RECORDINGS_DIR, exist_ok=True)

state = {
    "status": "idle",
    "pid": None,
    "process": None,
    "start_time": None,
    "pause_time": None,
    "elapsed_paused": 0,
    "current_file": None,
}
state_lock = threading.Lock()


def get_elapsed():
    with state_lock:
        if state["status"] == "idle":
            return 0
        if state["start_time"] is None:
            return 0
        if state["status"] == "paused":
            return state["elapsed_paused"]
        return int(time.time() - state["start_time"] + state["elapsed_paused"])


def _install_lxc_wrapper():
    """
    Install a shim over /usr/bin/lxc-start that fixes LXC 5.0 / Waydroid 1.6.x
    mount-path resolution on every container start.

    Why a wrapper and not a config patch:
      Waydroid regenerates config_nodes and config_session fresh on every
      waydroid-container start, so any pre-patch is immediately overwritten.
      The wrapper intercepts lxc-start — called by Waydroid after config
      generation — rewrites all relative lxc.mount.entry targets to absolute
      paths inside the container rootfs, then execs the real lxc-start.
      This covers both config files, both tmpfs and bind mounts, and survives
      Waydroid upgrades automatically.

    Idempotent: safe to call on every Start click.
    """
    already = os.path.exists(LXC_START_REAL)
    msgs = []

    try:
        if not already:
            # Back up the real lxc-start
            os.rename(LXC_START_WRAPPER, LXC_START_REAL)
            msgs.append(f"Backed up lxc-start → lxc-start.real")

        # (Re)write the wrapper — idempotent
        with open(LXC_START_WRAPPER, "w") as f:
            f.write(LXC_WRAPPER_SCRIPT)
        os.chmod(LXC_START_WRAPPER, 0o755)
        msgs.append("lxc-start wrapper installed (LXC 5.0 mount-path shim)")
        return True, msgs

    except Exception as e:
        # If we already moved the real binary but writing the wrapper failed,
        # restore it so the system isn't broken
        if not already and os.path.exists(LXC_START_REAL) and not os.path.exists(LXC_START_WRAPPER):
            try:
                os.rename(LXC_START_REAL, LXC_START_WRAPPER)
            except Exception:
                pass
        return False, [str(e)]


def _waydroid_pre_start():
    """
    Create host-side resources that must exist before the LXC container starts.

    1.  /run/user/0/pulse/  — PulseAudio socket directory.
        config_session binds /run/user/0/pulse/native into the container.
        With the lxc-start wrapper the target is now absolute, so LXC needs
        the host source path to exist (even as an empty dir) before attempting
        the bind mount; otherwise it aborts.  The socket itself is optional
        (bind,optional in config_session) so the container starts fine without
        an active PulseAudio daemon — audio simply won't work.

    2.  waydroid0 network bridge — Waydroid uses this bridge for container
        networking.  If it doesn't exist before lxc-start, the container
        network setup fails.  We replicate what `waydroid-net` / ip commands
        would do: create a bridge, assign 192.168.250.1/24, bring it up.
    """
    msgs = []

    # --- PulseAudio socket directory ---
    pulse_dir = "/run/user/0/pulse"
    try:
        os.makedirs(pulse_dir, mode=0o700, exist_ok=True)
        msgs.append(f"Ensured {pulse_dir} exists")
    except Exception as e:
        msgs.append(f"Warning: could not create {pulse_dir}: {e}")

    # --- waydroid0 bridge ---
    try:
        chk = subprocess.run(
            ["ip", "link", "show", "waydroid0"],
            capture_output=True, text=True
        )
        if chk.returncode != 0:
            # Bridge doesn't exist — create it
            for cmd in [
                ["ip", "link", "add", "waydroid0", "type", "bridge"],
                ["ip", "addr", "add", "192.168.250.1/24", "dev", "waydroid0"],
                ["ip", "link", "set", "waydroid0", "up"],
            ]:
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode != 0:
                    msgs.append(f"Warning: {' '.join(cmd)}: {r.stderr.strip()}")
                    break
            else:
                msgs.append("waydroid0 bridge created (192.168.250.1/24)")
        else:
            # Ensure it's up
            subprocess.run(["ip", "link", "set", "waydroid0", "up"],
                           capture_output=True)
            msgs.append("waydroid0 bridge already present — brought up")
    except Exception as e:
        msgs.append(f"Warning: waydroid0 bridge setup failed: {e}")

    return msgs


def _waydroid_status():
    """Return a dict with waydroid container/session status."""
    container = "stopped"
    session = "stopped"

    try:
        r = subprocess.run(
            ["systemctl", "is-active", "waydroid-container"],
            capture_output=True, text=True, timeout=5
        )
        if r.stdout.strip() == "active":
            container = "running"
    except Exception:
        pass

    try:
        r = subprocess.run(
            ["waydroid", "status"],
            capture_output=True, text=True, timeout=5
        )
        if "Session:\tRUNNING" in r.stdout:
            session = "running"
        elif "Session:\tSTOPPED" in r.stdout:
            session = "stopped"
    except Exception:
        pass

    return {"container": container, "session": session}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    with state_lock:
        status = state["status"]
        current_file = state["current_file"]
    return jsonify({
        "status": status,
        "elapsed": get_elapsed(),
        "current_file": os.path.basename(current_file) if current_file else None,
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    with state_lock:
        if state["status"] not in ("idle",):
            return jsonify({"error": f"Cannot start: status is {state['status']}"}), 400

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(RECORDINGS_DIR, f"recording_{timestamp}.mp4")

        cmd = [
            "ffmpeg", "-y",
            "-f", "x11grab",
            "-r", FRAMERATE,
            "-s", RESOLUTION,
            "-i", f"{DISPLAY}.0",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            filename
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            state["status"] = "recording"
            state["pid"] = proc.pid
            state["process"] = proc
            state["start_time"] = time.time()
            state["pause_time"] = None
            state["elapsed_paused"] = 0
            state["current_file"] = filename
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return jsonify({"status": "recording", "file": os.path.basename(filename)})


@app.route("/api/pause", methods=["POST"])
def api_pause():
    # NOTE: SIGSTOP freezes the ffmpeg process mid-write. The MP4 file remains
    # open and incomplete while paused. On most Linux systems this is safe for
    # short pauses, but leaving a recording paused for extended periods risks
    # file handle issues or partial-write corruption on some kernels/filesystems.
    # Always stop the recording rather than pausing for more than a few minutes.
    with state_lock:
        if state["status"] != "recording":
            return jsonify({"error": "Not currently recording"}), 400
        if state["process"] is None:
            return jsonify({"error": "No active process"}), 400

        try:
            os.kill(state["pid"], signal.SIGSTOP)
            state["elapsed_paused"] += int(time.time() - state["start_time"])
            state["pause_time"] = time.time()
            state["start_time"] = None
            state["status"] = "paused"
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return jsonify({"status": "paused"})


@app.route("/api/resume", methods=["POST"])
def api_resume():
    with state_lock:
        if state["status"] != "paused":
            return jsonify({"error": "Not paused"}), 400
        if state["process"] is None:
            return jsonify({"error": "No active process"}), 400

        try:
            os.kill(state["pid"], signal.SIGCONT)
            state["start_time"] = time.time()
            state["pause_time"] = None
            state["status"] = "recording"
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return jsonify({"status": "recording"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    with state_lock:
        if state["status"] not in ("recording", "paused"):
            return jsonify({"error": "Not recording"}), 400
        if state["process"] is None:
            return jsonify({"error": "No active process"}), 400

        proc = state["process"]
        pid = state["pid"]
        saved_file = state["current_file"]

        try:
            if state["status"] == "paused":
                os.kill(pid, signal.SIGCONT)
            proc.stdin.write(b"q")
            proc.stdin.flush()
        except Exception:
            pass

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

        state["status"] = "idle"
        state["pid"] = None
        state["process"] = None
        state["start_time"] = None
        state["pause_time"] = None
        state["elapsed_paused"] = 0
        state["current_file"] = None

    return jsonify({"status": "idle", "saved_file": os.path.basename(saved_file) if saved_file else None})


@app.route("/api/recordings")
def api_recordings():
    files = sorted(
        glob.glob(os.path.join(RECORDINGS_DIR, "*.mp4")),
        key=os.path.getmtime,
        reverse=True,
    )
    recordings = []
    for f in files:
        stat = os.stat(f)
        recordings.append({
            "filename": os.path.basename(f),
            "size": stat.st_size,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return jsonify({"recordings": recordings})


@app.route("/api/upload/<filename>", methods=["POST"])
def api_upload(filename):
    filepath = os.path.join(RECORDINGS_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404

    safe_name = os.path.basename(filename)
    if safe_name != filename:
        return jsonify({"error": "Invalid filename"}), 400

    try:
        result = subprocess.run(
            ["rclone", "copy", filepath, "gdrive:DroidRecord/"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            return jsonify({"error": result.stderr or "rclone failed"}), 500

        os.remove(filepath)
        return jsonify({"status": "uploaded", "filename": filename})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Upload timed out"}), 500
    except FileNotFoundError:
        return jsonify({"error": "rclone not installed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/delete/<filename>", methods=["DELETE"])
def api_delete(filename):
    safe_name = os.path.basename(filename)
    if safe_name != filename:
        return jsonify({"error": "Invalid filename"}), 400

    filepath = os.path.join(RECORDINGS_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404

    try:
        os.remove(filepath)
        return jsonify({"status": "deleted", "filename": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Waydroid control endpoints
# ---------------------------------------------------------------------------

@app.route("/api/waydroid/status")
def api_waydroid_status():
    return jsonify(_waydroid_status())


@app.route("/api/waydroid/start", methods=["POST"])
def api_waydroid_start():
    messages = []

    # Step 1: install the lxc-start wrapper shim (idempotent)
    # The wrapper intercepts every lxc-start call Waydroid makes, patches
    # both config_nodes and config_session to absolute paths after Waydroid
    # writes them, then forwards to the real lxc-start.  This is the only
    # reliable fix because Waydroid regenerates both files on every start.
    ok, shim_msgs = _install_lxc_wrapper()
    messages.extend(shim_msgs)
    if not ok:
        return jsonify({"error": shim_msgs[-1], "messages": messages}), 500

    # Step 2: create host resources LXC needs before container start
    # - /run/user/0/pulse/  (PulseAudio bind-mount source must exist)
    # - waydroid0 bridge    (container networking)
    pre_msgs = _waydroid_pre_start()
    messages.extend(pre_msgs)

    # Step 3: start the container service
    try:
        r = subprocess.run(
            ["systemctl", "start", "waydroid-container"],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            err = (r.stderr.strip() or r.stdout.strip() or
                   "Failed to start waydroid-container")
            messages.append(err)
            return jsonify({"error": err, "messages": messages}), 500
        messages.append("waydroid-container started")
    except subprocess.TimeoutExpired:
        return jsonify({
            "error": "Timed out starting waydroid-container",
            "messages": messages
        }), 500
    except Exception as e:
        return jsonify({"error": str(e), "messages": messages}), 500

    # Step 3: launch the Waydroid UI on the virtual display
    try:
        subprocess.Popen(
            ["waydroid", "show-full-ui"],
            env={**os.environ, "DISPLAY": DISPLAY},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        messages.append("waydroid show-full-ui launched on DISPLAY=" + DISPLAY)
    except Exception as e:
        messages.append(f"Warning: could not launch UI: {e}")

    return jsonify({"status": "started", "messages": messages})


@app.route("/api/waydroid/stop", methods=["POST"])
def api_waydroid_stop():
    messages = []

    # Terminate the session first
    try:
        subprocess.run(
            ["waydroid", "session", "stop"],
            capture_output=True, text=True, timeout=15
        )
        messages.append("waydroid session stopped")
    except Exception as e:
        messages.append(f"session stop warning: {e}")

    # Stop the container service
    try:
        r = subprocess.run(
            ["systemctl", "stop", "waydroid-container"],
            capture_output=True, text=True, timeout=20
        )
        if r.returncode != 0:
            err = r.stderr.strip() or "Failed to stop waydroid-container"
            messages.append(err)
            return jsonify({"error": err, "messages": messages}), 500
        messages.append("waydroid-container stopped")
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out stopping waydroid-container", "messages": messages}), 500
    except Exception as e:
        return jsonify({"error": str(e), "messages": messages}), 500

    return jsonify({"status": "stopped", "messages": messages})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6080, debug=False)
