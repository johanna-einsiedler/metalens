"""``maseminer`` — run the engine on one machine, one command, no account.

    maseminer                      # data in ~/.maseminer, opens http://127.0.0.1:8765
    maseminer --data-dir ./study   # keep everything next to the project
    maseminer --brand metalens     # the general Metalens surface instead of MASEMiner
    maseminer --port 9000 --no-browser

What runs: embedded PostgreSQL (``pgserver``, no install) in ``<data>/pg``, PDFs and page
images in ``<data>/artifacts``, extractions inline in the request (no Redis, no worker),
every request as the single local owner (``paperlens.localmode``). Nothing leaves the
machine except the model call, which goes to the provider you configure — or to a local
model when you point a custom base URL at Ollama / vLLM / LM Studio. Back up = copy the
data folder while the app is stopped.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import sys
import threading
import webbrowser
from pathlib import Path

DEFAULT_PORT = 8765


def _data_dir(arg: str | None) -> Path:
    raw = arg or os.environ.get("MASEMINER_HOME") or "~/.maseminer"
    d = Path(raw).expanduser().resolve()
    for sub in ("pg", "artifacts", "presets"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def _secret(d: Path) -> str:
    f = d / "secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32), encoding="utf-8")
        try:
            f.chmod(0o600)
        except OSError:
            pass
    return f.read_text(encoding="utf-8").strip()


_PG = None      # the embedded server handle: kept for the process lifetime, stopped on exit


def _start_postgres(d: Path) -> str:
    global _PG
    try:
        import pgserver
    except ImportError:  # pragma: no cover - packaging guard
        sys.exit("pgserver is not installed — `pip install maseminer` (or: pip install pgserver).")
    _prune_stale_handles(d / "pg")
    _PG = pgserver.get_server(str(d / "pg"))
    return _PG.get_uri()


def _prune_stale_handles(pgdata: Path) -> None:
    """pgserver stops Postgres only when the LAST handle goes; a crashed or killed earlier
    run leaves its pid in ``.handle_pids.json`` and the server would then never stop."""
    f = pgdata / ".handle_pids.json"
    if not f.exists():
        return
    try:
        import psutil
        pids = json.loads(f.read_text(encoding="utf-8") or "[]")
        live = [p for p in pids if isinstance(p, int) and psutil.pid_exists(p)]
        if live != pids:
            f.write_text(json.dumps(live), encoding="utf-8")
    except Exception:  # noqa: BLE001 - never block startup on the bookkeeping file
        pass


def _stop_postgres() -> None:
    global _PG
    if _PG is not None:
        try:
            _PG.cleanup()
        except Exception:  # noqa: BLE001 - best effort on the way out
            pass
        _PG = None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="maseminer", description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", help="where the database, PDFs and page images live (default ~/.maseminer)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("MASEMINER_PORT", DEFAULT_PORT)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--brand", default=os.environ.get("PAPERLENS_BRAND", "maseminer"), choices=("maseminer", "metalens"))
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--version", action="store_true", help="print the version and exit")
    a = ap.parse_args(argv)

    if a.version:
        from . import __version__
        print(__version__)
        return 0

    d = _data_dir(a.data_dir)
    # Everything below must be in the environment BEFORE the app package is imported.
    os.environ["PAPERLENS_LOCAL_MODE"] = "1"
    os.environ["PAPERLENS_INLINE_JOBS"] = "1"
    os.environ["PAPERLENS_STORAGE"] = "local"
    os.environ["PAPERLENS_STORAGE_ROOT"] = str(d / "artifacts")
    os.environ["PAPERLENS_PRESET_DIRS"] = str(d / "presets")
    os.environ["PAPERLENS_BRAND"] = a.brand
    os.environ["PAPERLENS_SECRET"] = _secret(d)
    os.environ.pop("PAPERLENS_BASIC_PASSWORD", None)
    # the embedded server, unless an explicit MASEMINER_DATABASE_URL points at your own Postgres
    os.environ["PAPERLENS_DATABASE_URL"] = os.environ.get("MASEMINER_DATABASE_URL") or _start_postgres(d)

    from . import localmode, records
    from . import __version__
    conn = records.connect()
    try:
        records.init_db(conn)
        localmode.ensure_local_user(conn)
    finally:
        conn.close()
    (d / "VERSION").write_text(__version__ + "\n", encoding="utf-8")

    import uvicorn
    from .app import app

    url = f"http://{a.host}:{a.port}"
    print(f"MASEMiner {__version__} — data in {d}\n  {url}\n  Ctrl-C stops it; copy the data folder to back it up.", flush=True)
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # uvicorn captures SIGTERM/SIGINT for a graceful shutdown, then re-raises them with the
    # handlers that were installed BEFORE it ran — so ours is what finally stops Postgres.
    def _term(signum, frame):  # noqa: ARG001
        _stop_postgres()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _term)
    try:
        uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    finally:
        _stop_postgres()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
