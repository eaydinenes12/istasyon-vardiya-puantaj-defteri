import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main
import prototype_launcher
class EasyLauncherTests(unittest.TestCase):
    def test_launcher_uses_the_main_application_data_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            production = root / "VardiyaPuantaj" / "vardiya_puantaj.sqlite3"
            with \
                    patch.object(prototype_launcher, "data_file", return_value=production):
                result = prototype_launcher.easy_data_file()
            self.assertEqual(result, production)

    def test_source_launcher_uses_application_data_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_file = root / "VardiyaPuantaj" / "vardiya_puantaj.sqlite3"
            with patch.object(prototype_launcher, "data_file", return_value=data_file):
                result = prototype_launcher.easy_data_file()
            self.assertEqual(result, data_file)

    def test_launcher_runs_real_application_in_easy_mode(self):
        target = Path("separate") / "vardiya_puantaj.sqlite3"
        with patch.object(prototype_launcher, "easy_data_file", return_value=target), \
                patch.object(prototype_launcher, "run_application", return_value=0) as run:
            self.assertEqual(prototype_launcher.main(), 0)
        run.assert_called_once_with(data_path=target, easy_mode=True)

    def test_desktop_uses_explicit_database_native_export_bridge_and_separate_browser_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "vardiya_puantaj.sqlite3"
            server = Mock(data_path=target, server_address=("127.0.0.1", 18767), internal_token="test")
            webview = Mock()
            with patch.object(main, "data_file", side_effect=AssertionError("Production path must not be used")), \
                    patch.object(main, "create_server", return_value=server) as create, \
                    patch.object(main.threading, "Thread"), \
                    patch.dict("sys.modules", {"webview": webview}):
                self.assertEqual(main.main(data_path=target, easy_mode=True), 0)
            self.assertEqual(create.call_args.args[0], target)
            self.assertTrue(server.easy_mode)
            self.assertEqual(server.save_excel.__self__, webview.create_window.call_args.kwargs["js_api"])
            self.assertEqual(server.save_form.__self__, server.save_excel.__self__)
            webview.start.assert_called_once_with(
                private_mode=False, storage_path=str(target.parent / "browser"),
            )
            server.shutdown.assert_called_once()
            server.server_close.assert_called_once()
            server.lock.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
