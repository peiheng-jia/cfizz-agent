#!/usr/bin/env python3
"""Apply a FigurePatch, persist the session, and optionally render the new version."""

import argparse
import json
from pathlib import Path

from cfizz.agent import (
    CfizzRenderAdapter,
    DataInspector,
    FigureSession,
    FigureSpecValidator,
    FileFigureSessionStore,
)
from cfizz.agent.bundled import DEMO_DATA_ROOT, load_demo_spec


def load_json(path: str):
    with Path(path).expanduser().resolve().open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", help="Initial FigureSpec JSON")
    parser.add_argument("patch", help="FigurePatch JSON")
    parser.add_argument("--session-id", default="demo")
    parser.add_argument("--session-dir", default="agent_sessions")
    parser.add_argument("--output-dir", default="agent_output")
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    inspector = DataInspector([str(project_root), str(DEMO_DATA_ROOT)])
    validator = FigureSpecValidator(inspector)
    spec_path = Path(args.spec).expanduser().resolve()
    spec = load_demo_spec() if spec_path.name == "figure-spec.integrated-demo.json" else load_json(args.spec)
    session = FigureSession(args.session_id, spec, validator=validator)
    revision = session.apply_patch(load_json(args.patch))
    session_file = FileFigureSessionStore(args.session_dir).save(session)

    response = {
        "session_id": session.session_id,
        "version_id": revision.version_id,
        "summary": revision.summary,
        "impact": revision.impact,
        "session_file": str(session_file),
        "history": session.history(),
    }
    if args.render:
        version_output_dir = Path(args.output_dir) / session.session_id / revision.version_id
        render = CfizzRenderAdapter(validator, str(version_output_dir)).render(session.current_spec)
        response["render"] = {
            "success": render.success,
            "artifacts": render.artifacts,
            "error": render.error,
        }

    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if response.get("render", {}).get("success", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
