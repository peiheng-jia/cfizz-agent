"""Isolated render worker used by the web application.

Each figure is rendered in its own process.  The web process can therefore
terminate a long-running CFIZZ calculation without leaving its only render
thread occupied, while progress is reported through a tiny atomic JSON file.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import traceback
from typing import Any, Dict

from .adapter import CfizzRenderAdapter
from .figure_spec import FigureSpecValidator
from .inspection import DataInspector


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(temporary, path)


def run_worker(request_path: Path, result_path: Path, progress_path: Path) -> int:
    try:
        with request_path.open("r", encoding="utf-8") as handle:
            request = json.load(handle)

        def report(stage: str, progress: int) -> None:
            _write_json(
                progress_path,
                {"stage": str(stage), "progress": max(0, min(99, int(progress)))},
            )

        report("starting_worker", 3)
        inspector = DataInspector(request.get("allowed_roots") or [str(Path.cwd())])
        validator = FigureSpecValidator(inspector)
        adapter = CfizzRenderAdapter(validator, request["output_dir"])
        result = adapter.render(request["spec"], progress_callback=report)
        _write_json(
            result_path,
            {
                "success": bool(result.success),
                "artifacts": list(result.artifacts),
                "error": result.error,
                "entrypoint": result.request.entrypoint if result.request else None,
            },
        )
        return 0
    except BaseException as exc:
        _write_json(
            result_path,
            {
                "success": False,
                "artifacts": [],
                "error": f"渲染子进程失败：{exc}",
                "traceback": traceback.format_exc(),
            },
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one isolated CFIZZ render job.")
    parser.add_argument("request")
    parser.add_argument("result")
    parser.add_argument("progress")
    arguments = parser.parse_args()
    return run_worker(
        Path(arguments.request),
        Path(arguments.result),
        Path(arguments.progress),
    )


if __name__ == "__main__":
    raise SystemExit(main())
