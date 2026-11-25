"""
Lightweight background daemon that watches for USB Galaxy Tab connections and
automatically launches the PC TabMonitor Studio GUI (app_gui.py) when the user
taps 'Start' inside the tablet app while the PC GUI is closed.
"""
import os
import sys
import time
import socket
import subprocess
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADB_PATH = os.path.join(BASE_DIR, "bin", "adb.exe")
APP_GUI_PATH = os.path.join(BASE_DIR, "app_gui.py")

VIDEO_PORT = 12351
TOUCH_PORT = 12352
TABLET_VIDEO_PORT = 12347
TABLET_TOUCH_PORT = 12348
CTRL_PORT = 12355
WATCHER_LOCK_PORT = 12356


def run_cmd_hidden(args, timeout=5):
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        startupinfo=startupinfo,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def launch_pc_gui(res_str="1920x1080"):
    try:
        r = run_cmd_hidden(["schtasks", "/run", "/tn", "TabMonitorGUI"], timeout=3)
        if r.returncode == 0:
            time.sleep(1.2)
            # Send AUTO_START with resolution to the newly launched elevated GUI
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect(("127.0.0.1", CTRL_PORT))
                s.sendall(f"AUTO_START_{res_str}".encode("utf-8"))
                s.close()
            except Exception:
                pass
            return
    except Exception:
        pass
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    subprocess.Popen(
        [pythonw, APP_GUI_PATH, "--auto-start", res_str],
        cwd=BASE_DIR,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
    )


def usb_forwarding_loop():
    last_serial = None
    while True:
        try:
            r = run_cmd_hidden([ADB_PATH, "devices"], timeout=3)
            lines = [l.strip() for l in r.stdout.splitlines() if "\t" in l]
            if lines and "device" in lines[0].split("\t")[1]:
                serial = lines[0].split("\t")[0]
                if serial != last_serial:
                    run_cmd_hidden([ADB_PATH, "forward", "--remove-all"])
                    run_cmd_hidden([ADB_PATH, "reverse", f"tcp:{TABLET_VIDEO_PORT}", f"tcp:{VIDEO_PORT}"])
                    run_cmd_hidden([ADB_PATH, "reverse", f"tcp:{TABLET_TOUCH_PORT}", f"tcp:{TOUCH_PORT}"])
                    run_cmd_hidden([ADB_PATH, "reverse", f"tcp:{CTRL_PORT}", f"tcp:{CTRL_PORT}"])
                    last_serial = serial
            else:
                last_serial = None
        except Exception:
            last_serial = None
        time.sleep(1.5)


def fallback_ctrl_listener():
    """
    When app_gui.py is NOT running, bind CTRL_PORT (12355) so that if the user opens
    the TabMonitor app on the tablet and taps Start, we catch the command, release
    CTRL_PORT, and immediately launch app_gui.py in --auto-start mode!
    """
    while True:
        srv = None
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", CTRL_PORT))
            srv.listen(2)
            srv.settimeout(2.0)

            while True:
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                cmd = conn.recv(256).decode("utf-8", errors="ignore").strip()
                conn.close()

                if cmd.startswith("AUTO_START"):
                    res_str = "1280x720" if "1280" in cmd else "1920x1080"
                    srv.close()
                    srv = None
                    launch_pc_gui(res_str)
                    time.sleep(4.0)
                    break
        except Exception:
            if srv:
                try:
                    srv.close()
                except Exception:
                    pass
            time.sleep(2.0)


def main():
    lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock_sock.bind(("127.0.0.1", WATCHER_LOCK_PORT))
        lock_sock.listen(1)
    except Exception:
        sys.exit(0)

    threading.Thread(target=usb_forwarding_loop, daemon=True).start()
    fallback_ctrl_listener()


if __name__ == "__main__":
    main()
