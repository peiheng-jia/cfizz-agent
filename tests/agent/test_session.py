import json
from pathlib import Path
import tempfile
import unittest

from cfizz.agent import FigureSession, FileFigureSessionStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_spec():
    with (PROJECT_ROOT / "docs/examples/figure-spec.integrated-demo.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def color_patch(color, summary=None):
    return {
        "summary": summary or f"color {color}",
        "operations": [{
            "op": "update",
            "target_kind": "layer",
            "target_id": "atac_normal_layer",
            "field": "style.color",
            "value": color,
        }],
    }


def current_color(session):
    return session.current_spec["panels"][2]["layers"][0]["style"]["color"]


class FigureSessionTests(unittest.TestCase):
    def test_undo_and_redo(self):
        session = FigureSession("figure_demo", load_spec())
        revision = session.apply_patch(color_patch("#111111"))
        self.assertEqual(revision.version_id, "v0002")
        self.assertEqual(current_color(session), "#111111")

        session.undo()
        self.assertEqual(current_color(session), "#666666")
        self.assertTrue(session.can_redo)

        session.redo()
        self.assertEqual(current_color(session), "#111111")

    def test_new_patch_after_undo_discards_redo_branch(self):
        session = FigureSession("figure_demo", load_spec())
        session.apply_patch(color_patch("#111111"))
        session.undo()
        session.apply_patch(color_patch("#222222"))

        self.assertFalse(session.can_redo)
        self.assertEqual(current_color(session), "#222222")
        self.assertEqual(len(session.history()), 2)

    def test_session_round_trip(self):
        session = FigureSession("figure_demo", load_spec())
        session.apply_patch(color_patch("#111111", "darken ATAC"))
        session.undo()

        with tempfile.TemporaryDirectory() as directory:
            store = FileFigureSessionStore(directory)
            path = store.save(session)
            loaded = store.load("figure_demo")

        self.assertTrue(path.name.endswith(".json"))
        self.assertEqual(loaded.current.version_id, "v0001")
        self.assertTrue(loaded.can_redo)
        loaded.redo()
        self.assertEqual(current_color(loaded), "#111111")


if __name__ == "__main__":
    unittest.main()
