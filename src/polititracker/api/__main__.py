"""Dev launcher: python -m polititracker.api

Exists because psycopg async cannot run on Windows' default ProactorEventLoop,
and modern uvicorn builds its own loop (ignoring the asyncio policy), so the
selector loop must be forced via loop_factory. The Docker image runs uvicorn
directly (Linux; no issue).
"""

import asyncio
import selectors
import sys

import uvicorn


def main() -> None:
    config = uvicorn.Config("polititracker.api.main:app", host="127.0.0.1", port=8000)
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        asyncio.run(
            server.serve(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
