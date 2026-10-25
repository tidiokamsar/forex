import unittest

from astra.config import AppConfig, ModeUnavailableError
from astra.models import RunMode


class AppConfigTests(unittest.TestCase):
    def test_empty_configuration_defaults_to_simulation(self):
        config = AppConfig.from_env({})
        self.assertIs(config.mode, RunMode.SIMULATION)
        self.assertEqual(config.host, "127.0.0.1")

    def test_demo_and_live_modes_are_unavailable(self):
        for mode in ("DEMO", "LIVE"):
            with self.subTest(mode=mode), self.assertRaises(ModeUnavailableError):
                AppConfig.from_env({"ASTRA_MODE": mode})

    def test_live_flag_cannot_unlock_execution(self):
        with self.assertRaises(ModeUnavailableError):
            AppConfig.from_env({"ASTRA_LIVE_ENABLED": "true"})

    def test_server_must_bind_to_loopback(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            AppConfig.from_env({"ASTRA_HOST": "0.0.0.0"})

    def test_port_must_be_valid(self):
        for port in ("0", "65536", "not-a-port"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                AppConfig.from_env({"ASTRA_PORT": port})


if __name__ == "__main__":
    unittest.main()


class VersionTests(unittest.TestCase):
    def test_package_version_matches_pyproject(self):
        import tomllib
        from pathlib import Path

        import astra

        pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(astra.__version__, pyproject["project"]["version"])
