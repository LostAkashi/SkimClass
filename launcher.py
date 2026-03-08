import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from streamlit.web.bootstrap import run


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _find_free_port(start: int = 8501, max_tries: int = 50) -> int:
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port found for Streamlit")


def main() -> None:
    base = _base_dir()
    app_file = base / "app.py"
    if not app_file.exists():
        raise FileNotFoundError(f"Missing app entry file: {app_file}")

    port = _find_free_port()
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    os.environ["STREAMLIT_SERVER_PORT"] = str(port)

    def _open_browser() -> None:
        time.sleep(1.2)
        webbrowser.open(f"http://127.0.0.1:{port}")

    threading.Thread(target=_open_browser, daemon=True).start()

    flag_options = {
        "server.headless": True,
        "server.port": port,
        "browser.gatherUsageStats": False,
        "server.address": "127.0.0.1",
    }

    # Streamlit bootstrap API differs by version.
    try:
        run(str(app_file), command_line="", args=[], flag_options=flag_options)
        return
    except TypeError:
        pass

    try:
        run(str(app_file), args=[], flag_options=flag_options, is_hello=False)
        return
    except TypeError:
        pass

    run(str(app_file), False, [], flag_options)


if __name__ == "__main__":
    main()
