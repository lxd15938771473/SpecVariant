"""Check portable discovery and selection of the native OpenSSL toolchain."""

import argparse
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import source_patch_agent as agent


class ToolDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="specvariant tools ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = {"PATH": "", "PATHEXT": ".EXE;.CMD;.BAT"}
        self.env_patch = patch.dict(os.environ, environment, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def tool(self, directory, name):
        suffix = ".exe" if os.name == "nt" else ""
        path = self.root / directory / (name + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        return str(path.resolve())

    def test_explicit_tool_overrides_path_with_spaces(self):
        automatic = self.tool("automatic", "gcc")
        explicit = self.tool("selected compiler", "gcc")
        os.environ["PATH"] = str(Path(automatic).parent)
        os.environ["SPECVARIANT_GCC"] = explicit
        self.assertEqual(agent.probe_local_tool("gcc")["path"], explicit)

    def test_invalid_explicit_tool_does_not_fall_back(self):
        automatic = self.tool("automatic", "gcc")
        os.environ["PATH"] = str(Path(automatic).parent)
        for invalid in (str(self.root / "missing.exe"), str(self.root), ""):
            with self.subTest(value=invalid):
                os.environ["SPECVARIANT_GCC"] = invalid
                result = agent.probe_local_tool("gcc")
                self.assertFalse(result["available"])
                self.assertIn("SPECVARIANT_GCC", result["stderr"])

    def test_command_name_override_is_resolved_on_path(self):
        explicit = self.tool("selected compiler", "gcc-selected")
        os.environ["PATH"] = str(Path(explicit).parent)
        os.environ["SPECVARIANT_GCC"] = Path(explicit).name
        self.assertEqual(agent.find_executable("gcc"), explicit)

    def test_searches_path_for_compatible_perl_and_skips_duplicates(self):
        first = self.tool("native perl", "perl")
        second = self.tool("compatible perl", "perl")
        os.environ["PATH"] = os.pathsep.join(
            str(Path(item).parent) for item in (first, first, second)
        )
        with patch.object(agent, "perl_supports_openssl_configure", side_effect=[False, True]) as check:
            result = agent.probe_local_tool("perl")
        self.assertEqual(result["path"], second)
        self.assertEqual([call.args[0] for call in check.call_args_list], [first, second])

    def test_incompatible_explicit_perl_does_not_fall_back(self):
        explicit = self.tool("native perl", "perl")
        automatic = self.tool("compatible perl", "perl")
        os.environ["PATH"] = str(Path(automatic).parent)
        os.environ["SPECVARIANT_PERL"] = explicit
        with patch.object(agent, "perl_supports_openssl_configure", return_value=False) as check:
            result = agent.probe_local_tool("perl")
        self.assertFalse(result["available"])
        self.assertIn("Configure checks", result["stderr"])
        check.assert_called_once_with(explicit)

    def test_missing_tools_are_unavailable(self):
        os.environ["PATH"] = str(self.root)
        self.assertFalse(agent.local_mingw_toolchain()["available"])

    def test_environment_report_honors_explicit_tool_outside_path(self):
        explicit = self.tool("selected compiler", "gcc")
        os.environ["PATH"] = str(self.root / "empty path")
        os.environ["SPECVARIANT_GCC"] = explicit
        result = agent.probe_environment(argparse.Namespace(backend="local"))
        self.assertEqual(result["local"]["gcc"]["path"], explicit)
        self.assertTrue(result["local"]["gcc"]["available"])

    def test_build_and_test_include_all_selected_tool_directories(self):
        selected = {
            name: self.tool(directory, name)
            for name, directory in (
                ("perl", "perl bin"), ("gcc", "compiler bin"), ("mingw32-make", "make bin")
            )
        }
        for name, path in selected.items():
            os.environ[agent.TOOL_ENV_VARS[name]] = path
        args = argparse.Namespace(
            target="openssl", backend="local", build_command=None, test_command=None
        )
        with patch.object(agent, "perl_supports_openssl_configure", return_value=True):
            build, test = agent.select_default_commands({"ir_id": "tool-discovery"}, args)
        for command in (build, test):
            self.assertTrue(command.startswith('set "PATH='))
            self.assertIn(';%PATH%" && ', command)
            for path in selected.values():
                self.assertIn(str(Path(path).parent), command)
            self.assertIn('"' + selected["mingw32-make"] + '"', command)
        self.assertIn('"' + selected["perl"] + '" Configure', build)
        self.assertIn('set "CC="' + selected["gcc"] + '"" && ', build)


if __name__ == "__main__":
    unittest.main()
