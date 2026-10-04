from pathlib import Path
import tempfile
import threading
import time
import unittest

from cfizz.agent.adapter import RenderRequest, RenderResult
from cfizz.agent.figure_spec import ValidationResult
from cfizz.agent.jobs import RenderJobManager, RenderOutcome


class RenderJobManagerTests(unittest.TestCase):
    def test_latest_attempt_is_scoped_to_session_and_version(self):
        manager = RenderJobManager(lambda *_: RenderOutcome(True, []))
        try:
            manager.submit("demo", "v0001", {})
            manager.submit("other", "v0002", {})
            first = manager.submit("demo", "v0002", {})
            latest = manager.submit("demo", "v0002", {})
            self.assertEqual(manager.latest_for_session("demo", "v0002").job_id, latest.job_id)
            self.assertNotEqual(manager.latest_for_session("demo", "v0002").job_id, first.job_id)
            self.assertIsNone(manager.latest_for_session("missing", "v0002"))
        finally:
            manager.shutdown()

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

    def test_reports_stage_estimate_and_cancels_running_job(self):
        started = threading.Event()

        def render(
            session_id,
            version_id,
            spec,
            *,
            progress_callback,
            cancel_event,
        ):
            progress_callback("aggregating_contacts", 25)
            started.set()
            cancel_event.wait(5)
            return RenderOutcome(False, [], "should be discarded after cancellation")

        manager = RenderJobManager(render)
        try:
            job = manager.submit(
                "demo",
                "v0001",
                {"figure_type": "compartment_saddle"},
            )
            self.assertEqual(job.estimated_total_seconds, 120)
            self.assertTrue(started.wait(2))
            running = manager.get(job.job_id).to_dict()
            self.assertEqual(running["stage"], "aggregating_contacts")
            self.assertTrue(running["cancellable"])
            self.assertIsNotNone(running["estimated_remaining_seconds"])

            requested = manager.cancel(job.job_id)
            self.assertTrue(requested.cancel_requested)
            for _ in range(100):
                current = manager.get(job.job_id)
                if current.status == "cancelled":
                    break
                time.sleep(0.01)
            self.assertEqual(current.status, "cancelled")
            payload = current.to_dict()
            self.assertFalse(payload["cancellable"])
            self.assertEqual(payload["artifacts"], [])
            self.assertIsNone(payload["error"])
        finally:
            manager.shutdown()

    def test_cancels_queued_job_before_rendering(self):
        first_started = threading.Event()
        release_first = threading.Event()
        rendered_versions = []

        def render(session_id, version_id, spec, *, progress_callback, cancel_event):
            rendered_versions.append(version_id)
            if version_id == "v0001":
                first_started.set()
                release_first.wait(2)
            return RenderOutcome(True, [])

        manager = RenderJobManager(render)
        try:
            first = manager.submit("demo", "v0001", {"figure_type": "hic_triangle"})
            self.assertTrue(first_started.wait(2))
            second = manager.submit("demo", "v0002", {"figure_type": "hic_triangle"})
            queued = manager.get(second.job_id).to_dict()
            self.assertEqual(queued["status"], "queued")
            self.assertEqual(queued["queue_position"], 1)
            self.assertGreaterEqual(queued["estimated_remaining_seconds"], 30)
            cancelled = manager.cancel(second.job_id)
            self.assertEqual(cancelled.status, "cancelled")
            release_first.set()
            for _ in range(100):
                if manager.get(first.job_id).status == "succeeded":
                    break
                time.sleep(0.01)
            self.assertEqual(rendered_versions, ["v0001"])
        finally:
            release_first.set()
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
