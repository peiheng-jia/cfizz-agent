"""Command-line entry point for the installed CFIZZ Agent web app."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cfizz-agent",
        description="Start the local CFIZZ Agent web workspace.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000).")
    parser.add_argument(
        "--data-root",
        action="append",
        metavar="PATH",
        help="Directory the Agent may read. Repeat for multiple directories.",
    )
    parser.add_argument(
        "--runtime-dir",
        metavar="PATH",
        help="Directory for sessions, caches and rendered artifacts (default: ./agent_runtime).",
    )
    parser.add_argument("--reload", action="store_true", help="Reload the server when source files change.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Parse options and run Uvicorn.

    The project/data roots are passed through environment variables before
    importing the FastAPI module.  This keeps the same behaviour for the
    installed console script and the source-tree helper in ``examples/agent``.
    """

    args = build_parser().parse_args(argv)
    project_root = Path.cwd().expanduser().resolve()
    os.environ.setdefault("CFIZZ_AGENT_PROJECT_ROOT", str(project_root))
    runtime_root = Path(args.runtime_dir or os.environ.get("CFIZZ_AGENT_RUNTIME", project_root / "agent_runtime"))
    os.environ["CFIZZ_AGENT_RUNTIME"] = str(runtime_root.expanduser().resolve())
    if args.data_root:
        roots = [str(Path(path).expanduser().resolve()) for path in args.data_root]
        os.environ["CFIZZ_AGENT_DATA_ROOTS"] = os.pathsep.join(roots)

    import uvicorn

    uvicorn.run(
        "cfizz.agent.web:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":  # pragma: no cover - exercised by the console script
    main()
