#!/usr/bin/env python3
"""Run the local CFIZZ Agent web workspace."""

import argparse
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.environ.setdefault("MPLCONFIGDIR", "/tmp/cfizz-agent-matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/cfizz-agent-cache")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--data-root",
        action="append",
        help="Directory the Agent may read. Repeat to authorize multiple roots.",
    )
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    if args.data_root:
        os.environ["CFIZZ_AGENT_DATA_ROOTS"] = os.pathsep.join(str(Path(path).expanduser().resolve()) for path in args.data_root)

    import uvicorn

    uvicorn.run(
        "cfizz.agent.web:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        app_dir=str(PROJECT_ROOT / "src"),
    )


if __name__ == "__main__":
    main()
