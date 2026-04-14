import os
import signal
import subprocess
import time
import glob
import threading
from datetime import datetime
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

RECORDINGS_DIR     = "/recordings"
DISPLAY            = ":1"
RESOLUTION         = "1280x720"
FRAMERATE          = "30"
REDROID_CONTAINER  = "redroid"
REDROID_ADB_SERIAL = "localhost:5555"

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


def _redroid_status():
    """Return dict with redroid container status and scrcpy running flag."""
    container = "unknown"
    scrcpy_running = False

    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", REDROID_CONTAINER],
            capture_output=True, text=True, timeout=5
        )
        container = r.stdout.strip() if r.returncode == 0 else "not found"
    except Exception:
        container = "unknown"

    try:
        r = subprocess.run(["pgrep", "-x", "scrcpy"], capture_output=True, timeout=3)
        scrcpy_running = r.returncode == 0
    except Exception:
        pass

    return {"container": container, "scrcpy": scrcpy_running}


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
# Redroid (Android in Docker) control endpoints
# ---------------------------------------------------------------------------

@app.route("/api/redroid/status")
def api_redroid_status():
    return jsonify(_redroid_status())


@app.route("/api/redroid/start", methods=["POST"])
def api_redroid_start():
    messages = []

    # Step 1: start the Docker container
    try:
        r = subprocess.run(
            ["docker", "start", REDROID_CONTAINER],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            err = r.stderr.strip() or r.stdout.strip() or "docker start failed"
            messages.append(err)
            return jsonify({"error": err, "messages": messages}), 500
        messages.append(f"Container '{REDROID_CONTAINER}' started")
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out starting redroid container", "messages": messages}), 500
    except Exception as e:
        return jsonify({"error": str(e), "messages": messages}), 500

    # Step 2: wait for ADB in the container then connect
    time.sleep(3)
    try:
        r = subprocess.run(
            ["adb", "connect", REDROID_ADB_SERIAL],
            capture_output=True, text=True, timeout=15
        )
        messages.append(f"adb: {(r.stdout.strip() or r.stderr.strip()) or 'connected'}")
    except Exception as e:
        messages.append(f"Warning: adb connect failed: {e}")

    # Step 3: launch scrcpy on the virtual display
    try:
        subprocess.Popen(
            ["scrcpy", "--serial", REDROID_ADB_SERIAL, "--no-audio"],
            env={**os.environ, "DISPLAY": DISPLAY},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        messages.append(f"scrcpy launched on DISPLAY={DISPLAY}")
    except Exception as e:
        messages.append(f"Warning: scrcpy launch failed: {e}")

    return jsonify({"status": "started", "messages": messages})


@app.route("/api/redroid/stop", methods=["POST"])
def api_redroid_stop():
    messages = []

    # Step 1: kill scrcpy
    try:
        r = subprocess.run(["pkill", "scrcpy"], capture_output=True, text=True)
        messages.append("scrcpy killed" if r.returncode == 0 else "scrcpy was not running")
    except Exception as e:
        messages.append(f"Warning: pkill scrcpy: {e}")

    # Step 2: stop the container
    try:
        r = subprocess.run(
            ["docker", "stop", REDROID_CONTAINER],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            err = r.stderr.strip() or "docker stop failed"
            messages.append(err)
            return jsonify({"error": err, "messages": messages}), 500
        messages.append(f"Container '{REDROID_CONTAINER}' stopped")
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out stopping redroid", "messages": messages}), 500
    except Exception as e:
        return jsonify({"error": str(e), "messages": messages}), 500

    return jsonify({"status": "stopped", "messages": messages})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6080, debug=False)
