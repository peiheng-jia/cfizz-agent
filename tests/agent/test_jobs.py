from pathlib import Path
import tempfile
import time
import unittest

from cfizz.agent.adapter import RenderRequest, RenderResult
from cfizz.agent.figure_spec import ValidationResult
from cfizz.agent.jobs import RenderJobManager


class RenderJobManagerTests(unittest.TestCase):
    def test_runs_job_and_records_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = str(Path(directory) / "figure.svg")
            request = RenderRequest("fake", {}, str(Path(directory) / "figure"), [artifact], ValidationResult({}))

            def render(session_id, version_id, spec):
                Path(artifact).touch()
                return RenderResult(True, [artifact], request)

            manager = RenderJobManager(render)
            try:
                job = manager.submit("demo", "v0001", {})
                for _ in range(100):
                    current = manager.get(job.job_id)
                    if current.status in {"succeeded", "failed"}:
                        break
                    time.sleep(0.01)
                self.assertEqual(current.status, "succeeded")
                self.assertEqual(current.artifacts, [artifact])
            finally:
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
