# type: ignore
"""
app.py — MamaGuard entry point.

The Streamlit dashboard that used to live here has been replaced by a FastAPI
backend (main.py) serving a vanilla HTML/CSS/JS frontend (static/). This file
is now just the launcher, so the familiar `python app.py` still starts the
whole product.

    python app.py                    # http://127.0.0.1:8000
    python app.py --reload           # auto-reload on source changes
    python app.py --port 9000        # different port
    python app.py --host 0.0.0.0     # expose on the LAN

Equivalent to:  uvicorn main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path
from threading import Timer

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
CHROMA_DIR = BASE_DIR / "chroma_db"


def preflight() -> None:
    """Warn loudly about anything that would make the app half-work."""
    import os

    from dotenv import load_dotenv

    load_dotenv()

    if not STATIC_DIR.is_dir():
        print(f"ERROR: frontend directory missing — expected {STATIC_DIR}", file=sys.stderr)
        raise SystemExit(1)

    if not CHROMA_DIR.is_dir():
        print(
            "WARNING: no ChromaDB store found. The Protocol Assistant will be "
            "unavailable until you run:\n    python ingest.py"
        )

    if not os.getenv("GROQ_API_KEY"):
        print(
            "WARNING: GROQ_API_KEY is not set. Add it to your .env file to "
            "enable the Protocol Assistant (/chat)."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the MamaGuard FastAPI server")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()

    preflight()

    import uvicorn

    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    print(f"\n  🤰 MamaGuard — Maternal Risk Detection System")
    print(f"  Dashboard : {url}")
    print(f"  API docs  : {url}/docs\n")

    # The reloader re-executes this module in a child process; opening a tab
    # from there would spawn one on every code change.
    if not args.no_browser and not args.reload:
        Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(BASE_DIR)] if args.reload else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
