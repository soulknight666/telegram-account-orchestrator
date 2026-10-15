"""Launcher regression tests without changing configuration or running services."""
import importlib.util
import io
import sys
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class LauncherTests(unittest.TestCase):
    def test_doctor_reaches_no_start_for_success_and_failure(self):
        spec = importlib.util.spec_from_file_location(
            "tao_setup_test", Path(__file__).resolve().parents[1] / "setup.py")
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        env = {"TAM_MASTER_KEY": "x" * 44, "TAM_API_ID": "12345",
               "TAM_API_HASH": "fixture", "TAM_NO_AUTH": "1"}
        for code in (0, 1):
            with self.subTest(returncode=code), ExitStack() as stack:
                stack.enter_context(patch.object(sys, "argv", ["setup.py", "--auto",
                    "--no-venv", "--skip-install", "--no-start", "--no-token",
                    "--deploy", "local", "--frontend", "web"]))
                stack.enter_context(patch.object(launcher, "ENV_PATH", launcher.ROOT / "setup.py"))
                stack.enter_context(patch.object(launcher, "read_env", return_value=env.copy()))
                stack.enter_context(patch.object(launcher, "set_env"))
                stack.enter_context(patch.object(launcher, "choose_deploy_frontend", return_value=("local", "web")))
                run = stack.enter_context(patch.object(launcher.subprocess, "run", return_value=SimpleNamespace(returncode=code)))
                popen = stack.enter_context(patch.object(launcher.subprocess, "Popen"))
                output = stack.enter_context(redirect_stdout(io.StringIO()))
                launcher.main()
                run.assert_called_once_with([sys.executable, "-m", "tam.cli", "doctor", "--fix"], cwd=launcher.ROOT)
                popen.assert_not_called()
                self.assertIn("--no-start", output.getvalue())


if __name__ == "__main__":
    unittest.main()
