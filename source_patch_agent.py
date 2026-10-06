#!/usr/bin/env python3
"""Generate implementation-native test patches from SpecLitmus IR cases.

This is the source-patch agent for step 5. It consumes one or more IR cases,
plans how each case should lower into an implementation-native test, emits a
reviewable patch, and can optionally apply/build/run that patch in a copied
work tree.

The first target is OpenSSL. The implementation is deliberately conservative:
it writes artifacts for every case and only mutates a copied work tree unless
--in-place is explicitly requested.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_DIR = Path("output/generated_tests")
DEFAULT_WORK_ROOT = Path("output/source_patch_worktrees")
TOOL_ENV_VARS = {
    "perl": "SPECVARIANT_PERL",
    "gcc": "SPECVARIANT_GCC",
    "mingw32-make": "SPECVARIANT_MINGW32_MAKE",
}


@dataclass
class GeneratedPatch:
    files: dict[str, str]
    notes: list[str]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cases(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if path.suffix.lower() == ".jsonl":
        cases: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                row = json.loads(stripped)
                if not isinstance(row, dict) or "ir_id" not in row:
                    raise ValueError(f"Unsupported IR JSONL row at {path}:{line_no}")
                cases.append(row)
        return {"source": str(path)}, cases

    data = read_json(path)
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        return data.get("metadata", {"source": str(path)}), data["cases"]
    if isinstance(data, list):
        return {"source": str(path)}, data
    raise ValueError(f"Unsupported IR JSON shape: {path}")


def select_cases(cases: list[dict[str, Any]], ir_id: str | None, limit: int | None) -> list[dict[str, Any]]:
    selected = cases
    if ir_id:
        selected = [case for case in selected if case.get("ir_id") == ir_id]
        if not selected:
            raise ValueError(f"IR case not found: {ir_id}")
    if limit is not None:
        selected = selected[:limit]
    return selected


def slugify(value: Any, max_len: int = 72) -> str:
    text = str(value or "case").casefold()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return (text[:max_len].strip("_") or "case")


def case_capabilities(case: dict[str, Any]) -> list[str]:
    requirements = case.get("setup", {}).get("requirements", {}) or {}
    capabilities: list[str] = []
    for key, enabled in requirements.items():
        if enabled is True:
            capabilities.append(key)
    for hint in case.get("setup", {}).get("capability_hints", []) or []:
        if hint not in capabilities:
            capabilities.append(str(hint))
    for event in case.get("events", []) or []:
        mutation = event.get("mutation")
        if isinstance(mutation, dict) and mutation.get("type") not in capabilities:
            capabilities.append(str(mutation.get("type")))
    return capabilities


def classify_openssl_strategy(case: dict[str, Any]) -> dict[str, Any]:
    message_types = {str(item).casefold() for item in case.get("setup", {}).get("message_types", []) or []}
    capabilities = set(case_capabilities(case))
    role = str(case.get("variant", {}).get("role", ""))
    has_early_data = "0-rtt" in message_types or "earlydata" in message_types or "early_data" in message_types
    has_mutation = any(isinstance(event.get("mutation"), dict) for event in case.get("events", []) or [])

    if has_early_data and has_mutation:
        return {
            "strategy": "extend_sslapitest_early_data_harness",
            "target_test_area": "test/sslapitest.c and helpers/ssltestlib.c",
            "confidence": "medium",
            "limitations": [
                "requires native OpenSSL early-data APIs",
                "requires packet/mempacket control for duplicate or replay injection",
                "generated patch is a reviewable scaffold until C-level mutation code is completed",
            ],
            "build_command": "nmake test TESTS=test_sslapitest",
            "test_command": "nmake test TESTS=test_sslapitest",
        }

    if "clienthello" in message_types or role == "positive_control":
        return {
            "strategy": "tlsproxy_handshake_recipe",
            "target_test_area": "test/recipes with TLSProxy",
            "confidence": "medium",
            "limitations": [
                "validates that the generated case can be driven through OpenSSL TLSProxy",
                "does not yet force every parameter_hints extension at packet granularity",
            ],
            "build_command": "nmake build_sw",
            "test_command": "nmake test TESTS=test_speclitmus",
        }

    return {
        "strategy": "manual_native_test_required",
        "target_test_area": "unknown",
        "confidence": "low",
        "limitations": [
            "no OpenSSL lowering rule matched this IR case",
            "agent emitted a manifest and review notes only",
        ],
        "build_command": None,
        "test_command": None,
    }


def build_plan(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if args.target != "openssl":
        raise ValueError(f"Unsupported target: {args.target}")

    strategy = classify_openssl_strategy(case)
    source_root = str(args.source_root.resolve()) if args.source_root else None
    return {
        "ir_id": case.get("ir_id"),
        "variant_id": case.get("variant_id"),
        "family_id": case.get("family_id"),
        "base_litmus_id": case.get("base_litmus_id"),
        "name": case.get("name"),
        "target": args.target,
        "implementation": "openssl",
        "source_root": source_root,
        "mode": args.mode,
        "selected_strategy": strategy["strategy"],
        "target_test_area": strategy["target_test_area"],
        "strategy_confidence": strategy["confidence"],
        "required_capabilities": case_capabilities(case),
        "implementation_mapping": {
            "protocol": case.get("setup", {}).get("protocol"),
            "message_types": case.get("setup", {}).get("message_types", []),
            "events": [
                {
                    "event_id": event.get("event_id"),
                    "operation": event.get("operation"),
                    "message_type": event.get("message_type"),
                    "parameter_hints": event.get("parameter_hints", []),
                    "mutation": event.get("mutation"),
                    "expected_observation": event.get("expected_observation"),
                }
                for event in case.get("events", []) or []
            ],
            "oracle_kinds": {
                "setup_checks": [check.get("kind") for check in case.get("oracle", {}).get("setup_checks", []) or []],
                "normative_checks": [
                    check.get("kind") for check in case.get("oracle", {}).get("normative_checks", []) or []
                ],
                "observations": [obs.get("kind") for obs in case.get("oracle", {}).get("observations", []) or []],
            },
        },
        "files_to_add": [],
        "files_to_modify": [],
        "build_command": args.build_command or strategy["build_command"],
        "test_command": args.test_command or strategy["test_command"],
        "limitations": strategy["limitations"],
        "evidence_refs": [
            evidence.get("item_id")
            for evidence in case.get("evidence", []) or []
            if evidence.get("item_id")
        ],
    }


def render_openssl_patch(case: dict[str, Any], plan: dict[str, Any]) -> GeneratedPatch:
    slug = slugify(case.get("ir_id"))
    manifest_path = f"test/speclitmus/{slug}.json"
    recipe_path = f"test/recipes/99-test_speclitmus_{slug}.t"
    files = {
        manifest_path: render_manifest(case, plan),
        recipe_path: render_recipe(case, plan, slug),
    }
    notes = [
        "Generated as a source-patch-agent proposal.",
        "Review before applying to an upstream source tree.",
    ]
    if plan["selected_strategy"] == "extend_sslapitest_early_data_harness":
        notes.append("The early-data mutation case is emitted as a native-test scaffold with explicit limitations.")
    return GeneratedPatch(files=files, notes=notes)


def render_manifest(case: dict[str, Any], plan: dict[str, Any]) -> str:
    manifest = {
        "generated_by": "source_patch_agent.py",
        "ir_id": case.get("ir_id"),
        "variant_id": case.get("variant_id"),
        "name": case.get("name"),
        "variant": case.get("variant", {}),
        "events": case.get("events", []),
        "oracle": case.get("oracle", {}),
        "evidence": case.get("evidence", []),
        "plan": {
            "target": plan["target"],
            "selected_strategy": plan["selected_strategy"],
            "target_test_area": plan["target_test_area"],
            "limitations": plan["limitations"],
        },
    }
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def render_recipe(case: dict[str, Any], plan: dict[str, Any], slug: str) -> str:
    title = str(case.get("name", slug)).replace('"', "'")
    ir_id = str(case.get("ir_id", slug))
    strategy = plan["selected_strategy"]
    manifest = f"test/speclitmus/{slug}.json"
    parameter_hints = {
        str(hint).casefold()
        for event in case.get("events", []) or []
        for hint in event.get("parameter_hints", []) or []
    }
    role = str(case.get("variant", {}).get("role", "")).casefold()

    if strategy == "tlsproxy_handshake_recipe":
        if {"pre_shared_key", "key_share"}.issubset(parameter_hints) and role == "positive_control":
            return f'''#! /usr/bin/env perl
# Generated by SpecLitmus source_patch_agent.py
# IR: {ir_id}

use strict;
use OpenSSL::Test qw/:DEFAULT cmdstr srctop_file bldtop_dir/;
use OpenSSL::Test::Utils;
use File::Temp qw(tempfile);
use TLSProxy::Proxy;
use Cwd qw(abs_path);

setup("test_speclitmus_{slug}");

plan skip_all => "TLSProxy is not usable on $^O" if $^O =~ /^(VMS)$/;
plan skip_all => "SpecLitmus TLSProxy case needs module support"
    if disabled("module");
plan skip_all => "SpecLitmus TLSProxy case needs socket support"
    if disabled("sock");
plan skip_all => "SpecLitmus TLS 1.3 PSK case needs TLS, TLS 1.3, and EC/DH enabled"
    if alldisabled(available_protocols("tls")) || disabled("tls1_3")
       || (disabled("ec") && disabled("dh"));

my $module_dir = abs_path(bldtop_dir("test"));
if ($^O eq "cygwin") {{
    chomp(my $windows_module_dir = `cygpath -w "$module_dir"`);
    $module_dir = $windows_module_dir if $windows_module_dir ne "";
}}
$ENV{{OPENSSL_MODULES}} = $module_dir;

plan tests => 6;

ok(-f srctop_file("{manifest}"),
   "SpecLitmus manifest is present for {title}");

my $proxy = TLSProxy::Proxy->new(
    undef,
    cmdstr(app(["openssl"]), display => 1),
    srctop_file("apps", "server.pem"),
    (!$ENV{{HARNESS_ACTIVE}} || $ENV{{HARNESS_VERBOSE}})
);

(undef, my $session) = tempfile();
my $session_arg = $session;
if ($^O eq "cygwin") {{
    chomp(my $windows_session = `cygpath -w "$session"`);
    $session_arg = $windows_session if $windows_session ne "";
}}
$proxy->clientflags("-tls1_3 -sess_out ".$session_arg);
$proxy->serverflags("-tls1_3 -servername localhost");
$proxy->sessionfile($session);
ok($proxy->start() && TLSProxy::Message->success(),
   "SpecLitmus setup: initial TLS 1.3 connection obtains a resumable session");

$proxy->clear();
$proxy->clientflags("-tls1_3 -sess_in ".$session_arg);
$proxy->serverflags("-tls1_3 -servername localhost");
ok($proxy->start() && TLSProxy::Message->success(),
   "SpecLitmus oracle: server accepts the PSK resumption handshake");

my $resume_ch;
my $resume_sh;
foreach my $message (@{{$proxy->message_list}}) {{
    if (!defined $resume_ch
        && $message->mt() == TLSProxy::Message::MT_CLIENT_HELLO) {{
        $resume_ch = $message;
    }}
    if (!defined $resume_sh
        && $message->mt() == TLSProxy::Message::MT_SERVER_HELLO) {{
        $resume_sh = $message;
    }}
}}

ok(defined $resume_ch
   && defined $resume_ch->extension_data->{{TLSProxy::Message::EXT_PSK}},
   "SpecLitmus oracle: resumption ClientHello carries pre_shared_key");

ok(defined $resume_ch
   && defined $resume_ch->extension_data->{{TLSProxy::Message::EXT_KEY_SHARE}},
   "SpecLitmus oracle: resumption ClientHello carries key_share");

ok(defined $resume_sh
   && defined $resume_sh->extension_data->{{TLSProxy::Message::EXT_PSK}},
   "SpecLitmus oracle: ServerHello selects PSK resumption");

unlink $session;
'''

        return f'''#! /usr/bin/env perl
# Generated by SpecLitmus source_patch_agent.py
# IR: {ir_id}

use strict;
use OpenSSL::Test qw/:DEFAULT cmdstr srctop_file bldtop_dir/;
use OpenSSL::Test::Utils;
use TLSProxy::Proxy;
use Cwd qw(abs_path);

setup("test_speclitmus_{slug}");

plan skip_all => "TLSProxy is not usable on $^O" if $^O =~ /^(VMS)$/;
plan skip_all => "SpecLitmus TLSProxy case needs module support"
    if disabled("module");
plan skip_all => "SpecLitmus TLSProxy case needs socket support"
    if disabled("sock");
plan skip_all => "SpecLitmus TLS 1.3 case needs TLS enabled"
    if alldisabled(available_protocols("tls")) || disabled("tls1_3");

my $module_dir = abs_path(bldtop_dir("test"));
if ($^O eq "cygwin") {{
    chomp(my $windows_module_dir = `cygpath -w "$module_dir"`);
    $module_dir = $windows_module_dir if $windows_module_dir ne "";
}}
$ENV{{OPENSSL_MODULES}} = $module_dir;

plan tests => 2;

ok(-f srctop_file("{manifest}"),
   "SpecLitmus manifest is present for {title}");

my $proxy = TLSProxy::Proxy->new(
    undef,
    cmdstr(app(["openssl"]), display => 1),
    srctop_file("apps", "server.pem"),
    (!$ENV{{HARNESS_ACTIVE}} || $ENV{{HARNESS_VERBOSE}})
);

$proxy->clientflags("-tls1_3");
$proxy->serverflags("-tls1_3");

ok($proxy->start(), "SpecLitmus TLSProxy handshake for {title}");
'''

    if strategy == "extend_sslapitest_early_data_harness":
        role = str(case.get("variant", {}).get("role", ""))
        return f'''#! /usr/bin/env perl
# Generated by SpecLitmus source_patch_agent.py
# IR: {ir_id}

use strict;
use OpenSSL::Test qw/:DEFAULT srctop_file/;
use OpenSSL::Test::Utils;

setup("test_speclitmus_{slug}");

plan skip_all => "SpecLitmus early-data native mutation harness is not enabled"
    unless $ENV{{SPECLITMUS_ENABLE_NATIVE_MUTATION}};
plan skip_all => "SpecLitmus TLS 1.3 early-data case needs TLS enabled"
    if alldisabled(available_protocols("tls")) || disabled("tls1_3");

plan tests => 2;

ok(-f srctop_file("{manifest}"),
   "SpecLitmus manifest is present for {title}");

# Lowering strategy:
# - Extend test/sslapitest.c near existing test_early_data_* tests.
# - Reuse setupearly_data_test(), SSL_write_early_data(), SSL_read_early_data().
# - For role '{role}', use helpers/ssltestlib.c mempacket controls to inject the
#   duplicate/replay event described in the manifest.
# This scaffold is intentionally skipped until the native C mutation hook is
# generated and reviewed.
ok(0, "native early-data mutation hook must be generated before execution");
'''

    return f'''#! /usr/bin/env perl
# Generated by SpecLitmus source_patch_agent.py
# IR: {ir_id}

use strict;
use OpenSSL::Test qw/:DEFAULT srctop_file/;

setup("test_speclitmus_{slug}");
plan tests => 1;

ok(-f srctop_file("{manifest}"),
   "SpecLitmus manifest is present for {title}");
'''


def render_patch(files: dict[str, str]) -> str:
    chunks: list[str] = []
    for relpath, content in files.items():
        diff = list(difflib.unified_diff(
            [],
            content.splitlines(),
            fromfile=f"a/{relpath}",
            tofile=f"b/{relpath}",
            lineterm="",
        ))
        chunks.extend(line + "\n" for line in diff)
    return "".join(chunks)


def write_artifacts(case_dir: Path, plan: dict[str, Any], patch: GeneratedPatch, result: dict[str, Any]) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    plan["files_to_add"] = sorted(patch.files)
    write_json(case_dir / "plan.json", plan)
    (case_dir / "patch.diff").write_text(render_patch(patch.files), encoding="utf-8")
    write_json(case_dir / "result.json", result)


def copy_source_tree(source_root: Path, work_root: Path, overwrite: bool) -> Path:
    if work_root.exists():
        if overwrite:
            robust_rmtree(work_root)
        else:
            raise FileExistsError(f"Work tree exists: {work_root}. Use --overwrite.")

    def ignore(_dir: str, names: list[str]) -> set[str]:
        skipped = {
            ".git",
            "__pycache__",
            ".pytest_cache",
            "tmp",
        }
        return {name for name in names if name in skipped or name.endswith(".pyc")}

    shutil.copytree(source_root, work_root, ignore=ignore)
    return work_root


def robust_rmtree(path: Path) -> None:
    def onerror(func: Any, target: str, _exc_info: Any) -> None:
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            func(target)
        except Exception:
            renamed = path.with_name(f"{path.name}.stale.{int(time.time())}")
            try:
                path.rename(renamed)
            except Exception:
                pass

    shutil.rmtree(path, onerror=onerror)


def apply_generated_files(work_tree: Path, files: dict[str, str]) -> list[str]:
    written: list[str] = []
    for relpath, content in files.items():
        target = work_tree / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(str(target))
    return written


def run_command(command: str | None, cwd: Path, timeout: int) -> dict[str, Any]:
    if not command:
        return {
            "status": "not_run",
            "command": None,
            "exit_code": None,
            "stdout": "",
            "stderr": "no command configured",
        }
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        return {
            "status": "pass" if completed.returncode == 0 else "fail",
            "command": command,
            "exit_code": completed.returncode,
            "duration_seconds": round(time.time() - started, 3),
            "stdout": completed.stdout[-20000:],
            "stderr": completed.stderr[-20000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "command": command,
            "exit_code": None,
            "duration_seconds": round(time.time() - started, 3),
            "stdout": (exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-20000:] if isinstance(exc.stderr, str) else "",
        }


def windows_path_to_wsl(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    tail = resolved.relative_to(resolved.anchor).as_posix()
    return f"/mnt/{drive}/{tail}"


def shell_quote_single(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def run_wsl_command(command: str | None, cwd: Path, timeout: int, distro: str) -> dict[str, Any]:
    if not command:
        return {
            "status": "not_run",
            "command": None,
            "exit_code": None,
            "stdout": "",
            "stderr": "no command configured",
        }
    wsl_cwd = windows_path_to_wsl(cwd)
    bash_command = f"cd {shell_quote_single(wsl_cwd)} && {command}"
    full_command = ["wsl", "-d", distro, "bash", "-lc", bash_command]
    started = time.time()
    try:
        completed = subprocess.run(
            full_command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        return {
            "status": "pass" if completed.returncode == 0 else "fail",
            "command": command,
            "backend_command": " ".join(full_command),
            "backend": "wsl",
            "wsl_distro": distro,
            "exit_code": completed.returncode,
            "duration_seconds": round(time.time() - started, 3),
            "stdout": completed.stdout[-20000:],
            "stderr": completed.stderr[-20000:],
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return {
            "status": "timeout",
            "command": command,
            "backend_command": " ".join(full_command),
            "backend": "wsl",
            "wsl_distro": distro,
            "exit_code": None,
            "duration_seconds": round(time.time() - started, 3),
            "stdout": stdout[-20000:],
            "stderr": stderr[-20000:],
        }


def probe_command(command: list[str], timeout: int = 20) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        return {
            "available": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
    except Exception as exc:
        return {
            "available": False,
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
        }


def executable_candidates(name: str) -> list[str]:
    """Resolve an explicit tool setting, or search each directory on PATH."""
    env_var = TOOL_ENV_VARS.get(name)
    configured = os.environ.get(env_var) if env_var else None
    if configured is not None:
        found = shutil.which(configured) if configured else None
        return [str(Path(found).resolve())] if found else []

    candidates: list[str] = []
    seen: set[str] = set()
    for directory in os.get_exec_path():
        found = shutil.which(name, path=directory or os.curdir)
        if found:
            candidate = str(Path(found).resolve())
            normalized = os.path.normcase(candidate)
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(candidate)
    return candidates


def find_executable(name: str) -> str | None:
    candidates = executable_candidates(name)
    return candidates[0] if candidates else None


def perl_supports_openssl_configure(perl: str) -> bool:
    try:
        completed = subprocess.run(
            [
                perl,
                "-MIPC::Cmd",
                "-MLocale::Maketext::Simple",
                "-MFile::Spec",
                "-e",
                "print qq($^O\\n); print File::Spec->catfile(qw(a b)), qq(\\n)",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10,
        )
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        return completed.returncode == 0 and "MSWin32" not in lines and "a/b" in lines
    except Exception:
        return False


def quote_cmd_path(path: str) -> str:
    return f'"{path}"'


def probe_local_tool(name: str) -> dict[str, Any]:
    path = None
    candidates = executable_candidates(name)
    if name == "perl":
        for candidate in candidates:
            if perl_supports_openssl_configure(candidate):
                path = candidate
                break
    else:
        path = candidates[0] if candidates else None
    if not path:
        env_var = TOOL_ENV_VARS.get(name)
        location = env_var if env_var and env_var in os.environ else "PATH"
        reason = (
            "no Perl installation satisfies the OpenSSL MinGW Configure checks"
            if candidates and name == "perl"
            else "no executable found"
        )
        return {
            "available": False,
            "path": None,
            "exit_code": None,
            "stdout": "",
            "stderr": f"{reason} ({location})",
        }
    return {
        "available": True,
        "path": path,
        "exit_code": 0,
        "stdout": path + "\n",
        "stderr": "",
    }


def local_mingw_toolchain() -> dict[str, Any]:
    tools = {
        "perl": probe_local_tool("perl"),
        "gcc": probe_local_tool("gcc"),
        "mingw32-make": probe_local_tool("mingw32-make"),
    }
    return {
        "available": all(tool["available"] for tool in tools.values()),
        "tools": tools,
    }


def probe_environment(args: argparse.Namespace, source_root: Path | None = None) -> dict[str, Any]:
    probes: dict[str, Any] = {
        "backend": args.backend,
        "platform": sys.platform,
        "local": {},
        "wsl": {},
        "docker": {},
        "source_tree": {},
    }
    for tool in ("perl", "nmake", "cl", "make", "docker", "wsl", "gcc", "mingw32-make"):
        probes["local"][tool] = probe_local_tool(tool)
    probes["local"]["known_mingw_toolchain"] = local_mingw_toolchain()
    if args.backend in {"auto", "docker"}:
        probes["docker"]["info"] = probe_command(["docker", "info"], timeout=20)
    if args.backend in {"auto", "wsl"}:
        probes["wsl"]["status"] = probe_command(["wsl", "-l", "-v"], timeout=20)
        probes["wsl"]["toolchain"] = probe_command(
            [
                "wsl",
                "-d",
                args.wsl_distro,
                "bash",
                "-lc",
                "command -v perl && command -v make && command -v gcc",
            ],
            timeout=30,
        )
    if source_root is not None:
        probes["source_tree"] = {
            "path": str(source_root),
            "has_configdata_pm": (source_root / "configdata.pm").exists(),
            "has_makefile": (source_root / "Makefile").exists() or (source_root / "makefile").exists(),
            "has_configure": (source_root / "Configure").exists(),
            "has_test_dir": (source_root / "test").exists(),
        }
    return probes


def select_default_commands(plan: dict[str, Any], args: argparse.Namespace) -> tuple[str | None, str | None]:
    if args.build_command or args.test_command:
        return args.build_command or plan.get("build_command"), args.test_command or plan.get("test_command")

    if args.target == "openssl" and args.backend in {"local", "auto"}:
        toolchain = local_mingw_toolchain()
        if toolchain["available"]:
            slug = slugify(plan.get("ir_id"))
            test_name = f"test_speclitmus_{slug}"
            perl = quote_cmd_path(toolchain["tools"]["perl"]["path"])
            gcc = quote_cmd_path(toolchain["tools"]["gcc"]["path"])
            make = quote_cmd_path(toolchain["tools"]["mingw32-make"]["path"])
            tool_dirs = list(dict.fromkeys(
                str(Path(toolchain["tools"][name]["path"]).parent)
                for name in ("perl", "gcc", "mingw32-make")
            ))
            path_prefix = f'set "PATH={";".join(tool_dirs)};%PATH%" && '
            build = (
                f'{path_prefix}set "CC={gcc}" && '
                f"if not exist configdata.pm ({perl} Configure mingw64 "
                f"no-shared no-makedepend no-fips) && {make} -j2 build_sw"
            )
            test = f"{path_prefix}{make} test TESTS={test_name}"
            return build, test

    if args.target == "openssl" and args.backend in {"wsl", "auto"}:
        slug = slugify(plan.get("ir_id"))
        test_name = f"test_speclitmus_{slug}"
        build = (
            "if [ ! -f configdata.pm ]; then "
            "perl Configure no-shared no-makedepend no-fips; "
            "fi && make -j2 build_sw"
        )
        test = f"HARNESS_JOBS=1 make test TESTS={test_name}"
        return build, test

    return plan.get("build_command"), plan.get("test_command")


def run_backend_command(command: str | None, cwd: Path, timeout: int, args: argparse.Namespace) -> dict[str, Any]:
    if args.backend == "wsl":
        return run_wsl_command(command, cwd=cwd, timeout=timeout, distro=args.wsl_distro)
    if args.backend == "auto":
        if local_mingw_toolchain()["available"]:
            return run_command(command, cwd=cwd, timeout=timeout)
        env = probe_environment(args, cwd)
        if env.get("wsl", {}).get("toolchain", {}).get("available"):
            return run_wsl_command(command, cwd=cwd, timeout=timeout, distro=args.wsl_distro)
    return run_command(command, cwd=cwd, timeout=timeout)


def verdict_from_execution(plan: dict[str, Any], build: dict[str, Any], test: dict[str, Any]) -> str:
    if plan["mode"] == "propose":
        return "proposed"
    if build["status"] in {"fail", "timeout"}:
        return "inconclusive"
    if test["status"] == "pass":
        return "pass"
    if test["status"] in {"fail", "timeout"}:
        return "fail"
    return "inconclusive"


def process_case(case: dict[str, Any], metadata: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    plan = build_plan(case, args)
    env_probe = probe_environment(args, args.source_root)
    build_command, test_command = select_default_commands(plan, args)
    plan["build_command"] = build_command
    plan["test_command"] = test_command
    plan["execution_backend"] = args.backend
    plan["wsl_distro"] = args.wsl_distro if args.backend in {"wsl", "auto"} else None
    patch = render_openssl_patch(case, plan)
    case_dir = args.output_dir / slugify(case.get("ir_id"))
    build_result = {
        "status": "not_run",
        "command": None,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
    }
    test_result = dict(build_result)
    applied_files: list[str] = []
    work_tree = None

    if args.mode in {"apply", "apply-build-run"}:
        if args.source_root is None:
            raise ValueError("--source-root is required for apply modes")
        if args.in_place:
            work_tree = args.source_root
        else:
            work_tree = args.work_root / slugify(case.get("ir_id")) / args.source_root.name
            copy_source_tree(args.source_root, work_tree, overwrite=args.overwrite)
        applied_files = apply_generated_files(work_tree, patch.files)

    if args.mode == "apply-build-run":
        assert work_tree is not None
        build_result = run_backend_command(plan.get("build_command"), cwd=work_tree, timeout=args.timeout, args=args)
        if build_result["status"] == "pass":
            test_result = run_backend_command(plan.get("test_command"), cwd=work_tree, timeout=args.timeout, args=args)
        else:
            test_result = {
                "status": "not_run",
                "command": plan.get("test_command"),
                "exit_code": None,
                "stdout": "",
                "stderr": "build did not pass",
            }

    result = {
        "ir_id": case.get("ir_id"),
        "variant_id": case.get("variant_id"),
        "name": case.get("name"),
        "target": args.target,
        "mode": args.mode,
        "phase": "propose" if args.mode == "propose" else "apply" if args.mode == "apply" else "apply-build-run",
        "patch_status": "generated" if args.mode == "propose" else "applied",
        "build_status": build_result["status"],
        "test_status": test_result["status"],
        "verdict": verdict_from_execution(plan, build_result, test_result),
        "artifact_dir": str(case_dir),
        "work_tree": str(work_tree) if work_tree else None,
        "applied_files": applied_files,
        "generated_files": sorted(patch.files),
        "patch_notes": patch.notes,
        "plan_summary": {
            "selected_strategy": plan["selected_strategy"],
            "target_test_area": plan["target_test_area"],
            "limitations": plan["limitations"],
        },
        "evidence_refs": plan["evidence_refs"],
        "build": build_result,
        "test": test_result,
        "environment": env_probe,
        "source_metadata": metadata,
    }
    write_artifacts(case_dir, plan, patch, result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate implementation-native test patches from SpecLitmus IR.")
    parser.add_argument("--ir", required=True, type=Path, help="Input IR JSON or JSONL.")
    parser.add_argument("--ir-id", default=None, help="Only process one IR case.")
    parser.add_argument("--target", default="openssl", choices=["openssl"], help="Implementation target.")
    parser.add_argument("--source-root", type=Path, default=None, help="Target implementation source tree.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--mode", choices=["propose", "apply", "apply-build-run"], default="propose")
    parser.add_argument("--build-command", default=None, help="Override build command for apply-build-run.")
    parser.add_argument("--test-command", default=None, help="Override test command for apply-build-run.")
    parser.add_argument("--backend", choices=["auto", "local", "wsl"], default="auto")
    parser.add_argument("--wsl-distro", default="Ubuntu")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--in-place", action="store_true", help="Apply directly to --source-root instead of a copy.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    metadata, cases = load_cases(args.ir)
    selected = select_cases(cases, args.ir_id, args.limit)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.work_root.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for case in selected:
        results.append(process_case(case, metadata, args))

    summary = {
        "input_ir": str(args.ir),
        "target": args.target,
        "mode": args.mode,
        "case_count": len(results),
        "results": [
            {
                "ir_id": result["ir_id"],
                "verdict": result["verdict"],
                "strategy": result["plan_summary"]["selected_strategy"],
                "artifact_dir": result["artifact_dir"],
            }
            for result in results
        ],
    }
    write_json(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
