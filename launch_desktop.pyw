"""Double-click launcher for the qpdf Studio GUI on Windows."""
import ctypes
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))


def run() -> None:
    try:
        from pdf_studio.qt_desktop import launch
        launch()
    except Exception:
        log_directory = HERE / "runtime_logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        log_file = log_directory / "startup.log"
        logging.basicConfig(filename=str(log_file), filemode="a", level=logging.ERROR)
        logging.exception("Failed to launch qpdf Studio")
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Unable to start qpdf Studio.\nCheck the local log:\n{log_file}",
            "qpdf Studio startup error",
            0x10,
        )
        raise


if __name__ == "__main__":
    run()
