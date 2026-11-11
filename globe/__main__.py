"""`python -m globe` serves Readout Globe on http://127.0.0.1:8091, reachable from this machine only.

Run it from the project root with the project's virtual environment. The warehouse is opened read-only,
so stop the server before rebuilding the warehouse (`make warehouse` or `make sites`).
"""

from __future__ import annotations

import sys

import duckdb
import uvicorn

from .app import HOST, PORT, create_app


def main() -> None:
    try:
        app = create_app()
    except (duckdb.Error, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Readout Globe cannot start: {exc}") from None
    print(f"Readout Globe: http://{HOST}:{PORT}", file=sys.stderr)
    uvicorn.run(app, host=HOST, port=PORT, reload=False, workers=1, proxy_headers=False, server_header=False,
                log_level="info")


if __name__ == "__main__":
    main()
