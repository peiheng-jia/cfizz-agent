"""Command-line entry point for the installed CFIZZ Agent web app."""

from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import sys
import tempfile
from typing import Optional, Sequence

from cfizz import __version__

from .bundled import DEMO_DATA_ROOT, load_demo_spec, resource_checks


_REQUIRED_MODULES = (
    ("数值计算", "numpy"),
    ("数据表", "pandas"),
    ("科学计算", "scipy"),
    ("绘图", "matplotlib"),
    ("Hi-C I/O", "cooler"),
    ("Hi-C 分析", "cooltools"),
    ("基因组区间", "bioframe"),
    ("TAD 阈值", "skimage"),
    ("BigWig", "pyBigWig"),
    ("GTF/Tabix", "pysam"),
    ("Web API", "fastapi"),
    ("Web 服务", "uvicorn"),
    ("数据模型", "pydantic"),
    ("模型客户端", "openai"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cfizz-agent",
        description="Start the local CFIZZ Agent web workspace.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check dependencies and bundled resources, then exit.",
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


def check_installation() -> bool:
    """Print an actionable installation report without starting the server."""
    failures: list[str] = []
    print(f"CFIZZ Agent {__version__} 安装检查")

    for label, module_name in _REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # Import errors can be raised by binary dependencies too.
            failures.append(f"{label}（{module_name}）：{exc}")

    missing_resources = []
    for label, path in resource_checks():
        if not path.is_file() or path.stat().st_size <= 0:
            missing_resources.append(f"{label}：{path}")
    failures.extend(f"内置资源缺失：{item}" for item in missing_resources)

    try:
        from .figure_spec import FigureSpecValidator

        demo = load_demo_spec()
        result = FigureSpecValidator().validate(demo, inspect_files=False)
        if not result.valid:
            failures.extend(f"FOXJ1 示例配置：{issue.message}" for issue in result.errors)
        if not all(Path(source["path"]).is_file() for source in demo.get("data_sources", [])):
            failures.append("FOXJ1 示例配置包含不存在的数据路径。")
    except Exception as exc:
        failures.append(f"FOXJ1 示例无法读取：{exc}")

    try:
        from .references import ReferenceRegistry

        with tempfile.TemporaryDirectory(prefix="cfizz-check-") as temporary:
            registry = ReferenceRegistry(Path(temporary), Path(temporary) / "cache")
            references = registry.catalog()
            hg38 = next((item for item in references if item.get("id") == "hg38"), None)
            if not hg38 or not hg38.get("complete") or int(hg38.get("gene_count") or 0) < 10_000:
                failures.append("内置 hg38 注释或基因索引不完整。")
    except Exception as exc:
        failures.append(f"内置 hg38 参考无法读取：{exc}")

    if failures:
        print("\n检查失败：", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print("\n请在 Python 3.11 的 Linux/macOS/WSL2 环境重新安装 cfizz[agent]。", file=sys.stderr)
        return False

    print(f"  依赖：{len(_REQUIRED_MODULES)} 项正常")
    print(f"  示例：FOXJ1（{DEMO_DATA_ROOT}）")
    print("  参考：hg38 / Ensembl 110 完整注释正常")
    print("检查通过，可以运行 cfizz-agent。")
    return True


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Parse options and run Uvicorn.

    The project/data roots are passed through environment variables before
    importing the FastAPI module.  This keeps the same behaviour for the
    installed console script and the source-tree helper in ``examples/agent``.
    """

    args = build_parser().parse_args(argv)
    if args.check:
        raise SystemExit(0 if check_installation() else 1)
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
