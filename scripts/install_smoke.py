#!/usr/bin/env python3
"""Black-box smoke test for an installed CFIZZ Agent wheel."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def _json_request(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _available_port() -> int:
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def main() -> int:
    port = _available_port()
    with tempfile.TemporaryDirectory(prefix="cfizz-installed-smoke-") as temporary:
        root = Path(temporary)
        runtime = root / "runtime"
        log_path = root / "server.log"
        environment = dict(os.environ)
        environment["MPLCONFIGDIR"] = str(root / "matplotlib")
        environment.pop("CFIZZ_AGENT_PROJECT_ROOT", None)
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    "cfizz-agent",
                    "--host", "127.0.0.1",
                    "--port", str(port),
                    "--runtime-dir", str(runtime),
                ],
                cwd=root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
        base_url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("CFIZZ Agent exited before becoming healthy")
                try:
                    if _json_request(f"{base_url}/api/health").get("status") == "ok":
                        break
                except (OSError, URLError, ValueError):
                    time.sleep(0.5)
            else:
                raise RuntimeError("CFIZZ Agent did not become healthy within 90 seconds")

            references = _json_request(f"{base_url}/api/references").get("references", [])
            hg38 = next((item for item in references if item.get("id") == "hg38"), None)
            if not hg38 or not hg38.get("complete") or int(hg38.get("gene_count") or 0) < 10_000:
                raise RuntimeError("Installed server does not expose the bundled complete hg38 reference")

            created = _json_request(
                f"{base_url}/api/sessions/demo",
                {"session_id": "installed_wheel_smoke"},
            )
            job_id = created.get("job", {}).get("job_id")
            if not job_id:
                raise RuntimeError("Demo endpoint did not submit a render job")

            deadline = time.monotonic() + 240
            job = {}
            while time.monotonic() < deadline:
                job = _json_request(f"{base_url}/api/jobs/{job_id}")
                if job.get("status") in {"succeeded", "failed"}:
                    break
                time.sleep(1)
            if job.get("status") != "succeeded":
                raise RuntimeError(f"Bundled demo render failed: {job.get('error') or job}")
            suffixes = {Path(path).suffix.lower() for path in job.get("artifacts", [])}
            if not {".svg", ".png"}.issubset(suffixes):
                raise RuntimeError(f"Demo artifacts are incomplete: {job.get('artifacts')}")

            print("Installed-wheel smoke test passed: health, hg38, FOXJ1 SVG and PNG")
            return 0
        except Exception:
            if log_path.is_file():
                print("\n--- CFIZZ Agent server log ---", file=sys.stderr)
                print(log_path.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
