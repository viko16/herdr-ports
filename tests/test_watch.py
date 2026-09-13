"""Isolated Bash 3.2 watcher tests; no plugin or real process inspection."""
import collections
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "herdr-ports"
MOCK = r'''#!/usr/bin/python3
import json, os, pathlib, subprocess, sys, time
p = pathlib.Path(os.environ["FIXTURE"])
name = pathlib.Path(sys.argv[0]).name
cfg = json.loads((p / "config").read_text())
tick = int((p / "tick").read_text())
with (p / "calls").open("a") as f:
    f.write(json.dumps([tick, name, sys.argv[1:]]) + "\n")
def value(key, default):
    result = default
    for start, item in cfg.get(key, []):
        if tick >= start:
            result = item
    return result
def table():
    rows = []
    for i in range(cfg.get("workspaces", 2)):
        rows.extend([f"{10000+i} 1 zsh", f"{20000+i} {10000+i} tnpm run serve",
                     f"{424242+i} {20000+i} node /shared/server.js"])
    return value("processes", "\n".join(rows))
def sockets():
    return cfg.get("sockets", [[424242+i, 3000+i] for i in range(cfg.get("workspaces", 2))])
if name == "python3":
    import importlib.util, io
    spec = importlib.util.spec_from_file_location("ports_processes", sys.argv[1])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class Kernel:
        def identity(self, pid):
            rows = {int(line.split()[0]): int(line.split()[1]) for line in table().splitlines()}
            if pid not in rows:
                raise OSError("missing")
            return tuple(value("identities", {}).get(str(pid), [rows[pid], pid, 0]))
        def environ(self, pid):
            env = value("environments", {}).get(str(pid), {"HERDR_ENV": "1"})
            if env is None:
                raise OSError("denied")
            return {k.encode(): v.encode() for k, v in env.items()}
    module.KernelProcesses = Kernel
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): pass
        def connect(self, path):
            assert path == os.environ["HERDR_SOCKET_PATH"]
        def sendall(self, raw):
            request = json.loads(raw)
            method = request["method"]
            with (p / "calls").open("a") as f:
                f.write(json.dumps([tick, "rpc", [method]]) + "\n")
            if method == "session.snapshot":
                if tick in cfg.get("snapshot_fail", []): raise OSError("failed")
                if tick in cfg.get("snapshot_empty_response", []):
                    self.response = b""; return
                if tick in cfg.get("snapshot_invalid", []):
                    self.response = b"invalid\n"; return
                panes = value("panes", [{"pane_id": "p"+str(i), "workspace_id": "w"+str(i),
                    "terminal_id": "t"+str(i), "cwd": "/shared", "label": "Sidebar"}
                    for i in range(cfg.get("workspaces", 2))])
                result = {"snapshot": {"panes": panes, "workspaces":
                    [{"workspace_id": "w"+str(i), "label": "space"+str(i)} for i in range(cfg.get("workspaces", 2))]}}
            elif method == "pane.process_info":
                if tick in cfg.get("info_fail", []): raise OSError("failed")
                pane = request["params"]["pane_id"]
                result = {"process_info": {"pane_id": pane, "shell_pid":
                    value("shells", {}).get(pane, 10000 + int(pane[1:]))}}
            elif method == "workspace.report_metadata":
                params = request["params"]
                with (p / "calls").open("a") as f:
                    f.write(json.dumps([tick, "metadata_rpc", params]) + "\n")
                clear = params["tokens"].get("ports") is None
                if tick in cfg.get("clear_fail" if clear else "post_fail", []):
                    self.response = (json.dumps({"id": request["id"], "error":
                        {"code": "failed", "message": "mock metadata failure"}}) + "\n").encode()
                    return
                result = {"type": "ok"}
            else:
                raise AssertionError("unexpected write method")
            self.response = (json.dumps({"id": request["id"], "result": result}) + "\n").encode()
        def makefile(self, mode): return io.BytesIO(self.response)
    # Keep RPC counts separate from executable launches. Socket framing itself
    # is also exercised against a real private Unix socket in test_processes.
    if hasattr(module, "socket"):
        module.socket.socket = lambda *args: Socket()
    sys.argv = sys.argv[1:]
    try:
        module.main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit(1)
elif name == "uname":
    print(cfg.get("platform", "Darwin"))
elif name == "netstat":
    if tick in cfg.get("scan_fail", []):
        sys.exit(1)
    if value("listeners", True):
        for pid, port in sockets():
            print(f"tcp4 0 0 127.0.0.1.{port} *.* LISTEN app:{pid}")
elif name == "lsof":
    if "-iTCP" in sys.argv:
        if tick in cfg.get("scan_fail", []):
            print("lsof: scan failed", file=sys.stderr)
            sys.exit(1)
        if not value("listeners", True):
            sys.exit(1)
        for pid, port in sockets():
            print(f"p{pid}\nn127.0.0.1:{port}")
        sys.exit(1 if cfg.get("scan_partial") else 0)
    if tick in cfg.get("cwd_fail", []):
        sys.exit(1)
    for pid, cwd in cfg.get("cwds", [[pid, value("pid_cwd", "/shared")] for pid, _ in sockets()]):
        print(f"p{pid}\nn{cwd}")
    if tick in cfg.get("cwd_partial", []):
        print("p424243")  # another PID exited before its cwd was read
        sys.exit(1)
elif name == "date":
    print(1000 + tick * 5)
elif name == "ps":
    if tick in cfg.get("ps_fail", []):
        sys.exit(1)
    print(table())
elif name == "sleep":
    time.sleep(cfg.get("delay", 0))
    (p / "tick").write_text(str(tick + 1))
    if cfg.get("descendant") and tick == 0:
        subprocess.Popen(["/usr/bin/python3", "-c",
            "import time; time.sleep(2)"], close_fds=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sys.exit(1 if tick + 1 >= cfg.get("polls", 7) else 0)
elif name == "herdr":
    if sys.argv[1:3] == ["api", "snapshot"]:
        if tick in cfg.get("snapshot_fail", []):
            sys.exit(1)
        if tick in cfg.get("snapshot_empty_response", []):
            sys.exit(0)
        if tick in cfg.get("snapshot_invalid", []):
            print("not json")
        else:
            panes = value("panes", [{"pane_id": "p" + str(i), "workspace_id": "w" + str(i),
                "terminal_id": "t" + str(i), "cwd": "/shared", "label": "Sidebar"}
                for i in range(cfg.get("workspaces", 2))])
            print(json.dumps({"result": {"snapshot": {"panes": panes, "workspaces":
                [{"workspace_id": "w"+str(i), "label": "space"+str(i)} for i in range(cfg.get("workspaces", 2))]}}}))
    elif sys.argv[1:3] == ["pane", "process-info"]:
        if tick in cfg.get("info_fail", []):
            sys.exit(1)
        pane = sys.argv[-1]
        pid = value("shells", {}).get(pane, 10000 + int(pane[1:]))
        print(json.dumps({"result": {"process_info": {"pane_id": pane, "shell_pid": pid}}}))
    else:
        clear = "--clear-token" in sys.argv
        sys.exit(1 if tick in cfg.get("clear_fail" if clear else "post_fail", []) else 0)
else:
    os.execv(cfg["real_commands"][name], [name] + sys.argv[1:])
'''


class WatchTests(unittest.TestCase):
    def fixture(self, **config):
        # Leave temporary fixtures to the OS; never delete via rm/rmtree.
        path = Path(tempfile.mkdtemp(prefix="herdr-ports-test-"))
        commands = ("awk", "sort", "cut", "comm", "id", "cat", "dirname", "basename", "perl", "paste", "readlink", "mktemp")
        config["real_commands"] = {name: shutil.which(name) for name in commands}
        (path / "config").write_text(json.dumps(config))
        (path / "tick").write_text("0")
        (path / "calls").touch()
        (path / "mock").write_text(MOCK)
        (path / "mock").chmod(0o755)
        for command in ("uname", "netstat", "lsof", "ps", "python3", "date", "sleep", "herdr") + commands:
            (path / command).symlink_to("mock")
        env = dict(os.environ, PATH=str(path) + ":" + os.environ["PATH"],
                   FIXTURE=str(path), HERDR_BIN_PATH=str(path / "herdr"),
                   HERDR_SOCKET_PATH=str(path / "session.sock"),
                   HERDR_PORTS_INTERVAL="5")
        return path, env

    def run_watch(self, path, env, script=SCRIPT):
        result = subprocess.run(["/bin/bash", str(script), "watch"], env=env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stderr)  # mock sleep ends run
        self.assertEqual(result.stderr, "")
        return self.calls(path)

    @staticmethod
    def calls(path):
        return [json.loads(line) for line in (path / "calls").read_text().splitlines()]

    @staticmethod
    def reports(calls, clear=False):
        reports = []
        for tick, name, args in calls:
            if name == "metadata_rpc":
                params = args
            elif name == "herdr" and args[:2] == ["workspace", "report-metadata"]:
                def flag(key):
                    return args[args.index(key) + 1]
                params = {
                    "workspace_id": args[2],
                    "source": flag("--source"),
                    "tokens": {"ports": None if "--clear-token" in args else flag("--token").split("=", 1)[1]},
                    "seq": int(flag("--seq")),
                }
                if "--ttl-ms" in args:
                    params["ttl_ms"] = int(flag("--ttl-ms"))
            else:
                continue
            if ((params["tokens"].get("ports") is None) == clear):
                reports.append((tick, params))
        return reports


    def test_complete_stable_polling_budget(self):
        path, env = self.fixture(polls=7, workspaces=20)
        calls = self.run_watch(path, env)
        counts = collections.Counter(name for _, name, _ in calls if name not in ("rpc", "metadata_rpc"))
        self.assertEqual({key: counts[key] for key in ("python3", "ps", "netstat", "lsof", "herdr")},
                         dict(python3=11, ps=7, netstat=7, lsof=0, herdr=0))
        self.assertEqual(collections.Counter(a[0] for _, n, a in calls if n == "rpc"),
                         {"session.snapshot": 7, "pane.process_info": 20, "workspace.report_metadata": 80})
        self.assertEqual([t for t, n, a in calls if n == "rpc" and a == ["pane.process_info"]], [0] * 20)
        self.assertEqual([t for t, _ in self.reports(calls)][::20], [0, 2, 4, 6])
        for _, params in self.reports(calls):
            self.assertEqual(params["source"], "ports")
            self.assertEqual(params["tokens"], {"ports": "↯"})
            self.assertEqual(params["ttl_ms"], 25000)
        # Include the singleton launcher's absolute Bash exec, not only PATH.
        self.assertEqual(sum(n not in ("rpc", "metadata_rpc") for _, n, _ in calls) + 1, 65)
        # Run the same shell-ancestry functionality from the immutable baseline.
        before_path, before_env = self.fixture(polls=7, workspaces=20)
        for name in ("herdr-ports", "ports_processes.py"):
            (before_path / name).write_bytes(subprocess.check_output(
                ["git", "show", "fc912b3:" + name], cwd=ROOT))
        before = self.run_watch(before_path, before_env, before_path / "herdr-ports")
        self.assertEqual(len(before) + 1, 208)
        self.assertEqual(sum(n == "herdr" and a[:1] == ["api"] for _, n, a in before), 7)
        self.assertEqual(sum(n == "herdr" and a[:1] == ["pane"] for _, n, a in before), 60)
        self.assertEqual(self.reports(before), self.reports(calls))
        print("\n7 polls / 20 panes: external exec 208 -> 65; API reads 67 -> 27 plus 80 socket metadata writes.")

    def test_idle_polls_do_not_read_herdr_or_processes(self):
        path, env = self.fixture(polls=4, listeners=[[0, False], [3, True]])
        calls = self.run_watch(path, env)
        self.assertTrue(all(t == 3 for t, n, _ in calls if n in ("herdr", "ps", "rpc")))
        self.assertEqual(sum(n == "python3" for _, n, _ in calls), 5)

    def test_snapshot_failures_preserve_leases_and_retry(self):
        for failure in ("snapshot_fail", "snapshot_invalid", "snapshot_empty_response", "ps_fail"):
            path, env = self.fixture(polls=5, **{failure: [3]})
            calls = self.run_watch(path, env)
            self.assertEqual(self.reports(calls, True), [])
            self.assertEqual([t for t, _ in self.reports(calls)][-2:], [4, 4])

    def test_process_info_failure_retries_next_poll(self):
        path, env = self.fixture(polls=4, info_fail=[0])
        calls = self.run_watch(path, env)
        self.assertEqual([t for t, _ in self.reports(calls)], [1, 1, 3, 3])
        self.assertEqual(self.reports(calls, True), [])
        self.assertEqual([t for t, n, a in calls if n == "rpc" and a == ["pane.process_info"]], [0, 1, 1])

    def test_socket_failure_does_not_clear(self):
        path, env = self.fixture(polls=3, scan_fail=[1], listeners=[[2, False]])
        calls = self.run_watch(path, env)
        self.assertEqual([t for t, _ in self.reports(calls, True)], [2, 2])

    def test_closed_panes_clear_immediately_not_after_cache_ttl(self):
        path, env = self.fixture(polls=3, panes=[[1, []]])
        calls = self.run_watch(path, env)
        self.assertEqual([t for t, _ in self.reports(calls, True)], [1, 1])
        self.assertEqual(sum(n == "rpc" and a == ["pane.process_info"] for _, n, a in calls), 2)

    def test_cwd_is_display_only_and_commands_preserved(self):
        path, env = self.fixture(polls=3, cwd_fail=[0, 1, 2])
        self.assertEqual(len(self.reports(self.run_watch(path, env))), 4)
        result = subprocess.run(["/bin/bash", "-c", 'source "$1"; build_rows "$FIXTURE"', "test", str(SCRIPT)],
                                env=env, capture_output=True, text=True, check=True)
        self.assertIn("node /shared/server.js", result.stdout)

    def test_lsof_partial_and_failure(self):
        for partial in ([], [0]):
            path, env = self.fixture(cwd_partial=partial)
            result = subprocess.run(["/bin/bash", "-c", 'source "$1"; printf "424242\\t3000\\n" | raw_pid_cwds',
                                     "test", str(SCRIPT)], env=env, capture_output=True, text=True, check=True)
            self.assertIn("424242\t/shared", result.stdout)
        path, env = self.fixture(polls=4, platform="Other", scan_partial=True,
                                 scan_fail=[1], listeners=[[2, False]])
        calls = self.run_watch(path, env)
        self.assertEqual([t for t, _ in self.reports(calls, True)], [2, 2])

    def test_changes_clear_immediately_and_retry(self):
        path, env = self.fixture(polls=5, listeners=[[1, False], [3, True]],
                                 clear_fail=[1], post_fail=[3])
        calls = self.run_watch(path, env)
        self.assertEqual([t for t, _ in self.reports(calls, True)], [1, 1, 2, 2])
        self.assertEqual([t for t, _ in self.reports(calls)], [0, 0, 3, 3, 4, 4])

    def test_failed_renewal_retries_next_poll(self):
        path, env = self.fixture(polls=5, post_fail=[0, 3])
        calls = self.run_watch(path, env)
        self.assertEqual([t for t, _ in self.reports(calls)][::2], [0, 1, 3, 4])

    def test_inherited_lock_lasts_until_last_child_exits(self):
        path, env = self.fixture(polls=1, descendant=True)
        self.run_watch(path, env)
        blocked = subprocess.run(["/bin/bash", str(SCRIPT), "watch"], env=env,
                                 capture_output=True, timeout=5)
        self.assertEqual(blocked.returncode, 0, blocked.stderr)
        self.assertEqual(sum(n == "netstat" for _, n, _ in self.calls(path)), 1)
        time.sleep(2.1)
        self.run_watch(path, env)
        self.assertEqual(sum(n == "netstat" for _, n, _ in self.calls(path)), 2)

    def test_concurrent_singleton_session_isolation_and_reacquisition(self):
        path, env = self.fixture(polls=2, delay=0.5)
        other, other_env = self.fixture(polls=2, delay=0.5)
        processes = [subprocess.Popen(["/bin/bash", str(SCRIPT), "watch"], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(12)]
        independent = subprocess.Popen(["/bin/bash", str(SCRIPT), "watch"], env=other_env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        codes = []
        for process in processes + [independent]:
            _, errors = process.communicate(timeout=15)
            self.assertEqual(errors, b"")
            codes.append(process.returncode)
        self.assertEqual(sorted(codes[:-1]), [0] * 11 + [1])
        self.assertEqual(codes[-1], 1)
        self.assertEqual(sum(n == "netstat" for _, n, _ in self.calls(path)), 2)
        self.assertEqual(sum(n == "netstat" for _, n, _ in self.calls(other)), 2)
        # The persistent lock inode from the exited owner must be reusable.
        self.run_watch(path, env)
        self.assertEqual(sum(n == "netstat" for _, n, _ in self.calls(path)), 3)

    def test_concurrent_event_hooks_start_one_watcher(self):
        path, env = self.fixture(polls=2, delay=0.5)
        hooks = [subprocess.Popen(["/bin/bash", str(SCRIPT), "ensure-watch"], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(12)]
        for hook in hooks:
            _, errors = hook.communicate(timeout=5)
            self.assertEqual(hook.returncode, 0, errors)
        deadline = time.monotonic() + 10
        while int((path / "tick").read_text() or "0") < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual((path / "tick").read_text(), "2")
        self.assertEqual(sum(n == "netstat" for _, n, _ in self.calls(path)), 2)

    def test_invalid_intervals_do_not_poll(self):
        for key in ("HERDR_PORTS_INTERVAL",):
            for value in ("", "0", "-1", "0.5", "abc", "08", "999999999999999999999", "3601"):
                path, env = self.fixture()
                env[key] = value
                for mode in ("watch", "ensure-watch"):
                    result = subprocess.run(["/bin/bash", str(SCRIPT), mode], env=env,
                                            capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(self.calls(path), [])


if __name__ == "__main__":
    unittest.main()