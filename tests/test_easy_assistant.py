import copy
import tempfile
import unittest
from pathlib import Path

import ui_settings
from storage import Store


class EasyAssistantSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "assistant.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def workspace(self):
        return {
            "name": "Puantaj kontrolü",
            "menu": {
                "order": list(ui_settings.MENU_VIEWS), "hidden": ["view:schedule"],
                "ring_enabled": True, "ring_size": 260, "ring_items": ["view:attendance"],
            },
            "ribbons": {"schedule": False, "attendance": True},
            "zoom": {"schedule": 55, "attendance": 90},
        }

    def test_workspace_and_guide_survive_new_connection_without_changing_records(self):
        workspace = self.workspace()
        guide = {"step": 6, "completed": [0, 1, 4], "dismissed": False}
        with Store(self.path) as store:
            person = store.add_person("Örnek Personel")
            ui_settings.save(store, {
                "easy_workspaces": [workspace], "easy_active_workspace": workspace["name"],
                "easy_guide": guide,
            })
        with Store(self.path) as store:
            settings = ui_settings.load(store)
            self.assertEqual(settings["easy_workspaces"], [workspace])
            self.assertEqual(settings["easy_active_workspace"], workspace["name"])
            self.assertEqual(settings["easy_guide"], guide)
            self.assertEqual(store.people(), [person])
            self.assertEqual(store.archive_months(), [])

    def test_invalid_workspace_update_is_atomic(self):
        workspace = self.workspace()
        bad_items = []
        for key, value in (("zoom", {"schedule": True, "attendance": 100}),
                           ("zoom", {"schedule": 0, "attendance": 100}),
                           ("zoom", {"schedule": float("nan"), "attendance": 100}),
                           ("ribbons", {"schedule": "yes", "attendance": True}),
                           ("menu", {"order": list(ui_settings.MENU_VIEWS), "hidden": ["tool:test"]}),
                           ("name", "")):
            item = copy.deepcopy(workspace)
            item[key] = value
            bad_items.append([item])
        bad_items.extend(([workspace, workspace], [workspace] * 21, "invalid"))
        with Store(self.path) as store:
            baseline = ui_settings.save(store, {"easy_workspaces": [workspace]})
            for value in bad_items:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    ui_settings.save(store, {"easy_workspaces": value, "easy_ribbons": {
                        "schedule": False, "attendance": False,
                    }})
                self.assertEqual(ui_settings.load(store), baseline)

    def test_guide_rejects_wrong_types_duplicate_and_out_of_range_steps(self):
        with Store(self.path) as store:
            baseline = ui_settings.load(store)
            for guide in (
                {"step": True, "completed": [], "dismissed": False},
                {"step": 8, "completed": [], "dismissed": False},
                {"step": 0, "completed": [1, 1], "dismissed": False},
                {"step": 0, "completed": [True], "dismissed": False},
                {"step": 0, "completed": [8], "dismissed": False},
                {"step": 0, "completed": [], "dismissed": 1},
                {"step": 0, "completed": [], "dismissed": False, "extra": 0},
            ):
                with self.subTest(guide=guide), self.assertRaises(ValueError):
                    ui_settings.save(store, {"easy_guide": guide})
                self.assertEqual(ui_settings.load(store), baseline)

    def test_active_workspace_must_exist_and_removal_requires_clearing_it(self):
        item = self.workspace()
        with Store(self.path) as store:
            with self.assertRaises(ValueError):
                ui_settings.save(store, {"easy_active_workspace": item["name"]})
            ui_settings.save(store, {"easy_workspaces": [item], "easy_active_workspace": item["name"]})
            with self.assertRaises(ValueError):
                ui_settings.save(store, {"easy_workspaces": []})
            result = ui_settings.save(store, {"easy_workspaces": [], "easy_active_workspace": ""})
            self.assertEqual(result["easy_workspaces"], [])
            self.assertEqual(result["easy_active_workspace"], "")
