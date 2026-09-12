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
BASELINE = "bada711cac11c2243600019446c8a68216415569"

# These executables only read/write a private temporary fixture. Time advances
# at sleep boundaries, so TTL assertions do not depend on wall-clock timing.
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
if name == "uname":
    print(cfg.get("platform", "Darwin"))
elif name == "netstat":
    if tick in cfg.get("scan_fail", []):
        sys.exit(1)
    if value("listeners", True):
        print("tcp4 0 0 127.0.0.1.3000 *.* LISTEN app:424242")
elif name == "lsof":
    if "-iTCP" in sys.argv:
        if tick in cfg.get("scan_fail", []):
            print("lsof: scan failed", file=sys.stderr)
            sys.exit(1)
        if not value("listeners", True):
            sys.exit(1)
        print("p424242\nn127.0.0.1:3000")
        sys.exit(1 if cfg.get("scan_partial") else 0)
    if tick in cfg.get("cwd_fail", []):
        sys.exit(1)
    print("p424242\nn" + value("pid_cwd", "/project"))
    if tick in cfg.get("cwd_partial", []):
        print("p424243")  # another PID exited before its cwd was read
        sys.exit(1)
elif name == "date":
    print(1000 + tick * 5)
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
            print(json.dumps([{"pane_id": "p" + str(i), "workspace_id": "w" + str(i),
                "cwd": value("workspace_cwd", "/project")}
                for i in range(cfg.get("workspaces", 2))]))
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
        commands = ("awk", "sort", "cut", "jq", "comm", "id", "cat", "dirname", "basename", "perl", "paste", "readlink")
        config["real_commands"] = {name: shutil.which(name) for name in commands}
        (path / "config").write_text(json.dumps(config))
        (path / "tick").write_text("0")
        (path / "calls").touch()
        (path / "mock").write_text(MOCK)
        (path / "mock").chmod(0o755)
        for command in ("uname", "netstat", "lsof", "date", "sleep", "herdr") + commands:
            (path / command).symlink_to("mock")
        env = dict(os.environ, PATH=str(path) + ":" + os.environ["PATH"],
                   FIXTURE=str(path), HERDR_BIN_PATH=str(path / "herdr"),
                   HERDR_SOCKET_PATH=str(path / "session.sock"),
                   HERDR_PORTS_INTERVAL="5", HERDR_PORTS_WORKSPACE_INTERVAL="15")
        return path, env

    def run_watch(self, path, env):
        result = subprocess.run(["/bin/bash", str(SCRIPT), "watch"], env=env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stderr)  # mock sleep ends run
        self.assertEqual(result.stderr, "")
        return self.calls(path)

    @staticmethod
    def calls(path):
        return [json.loads(line) for line in (path / "calls").read_text().splitlines()]

    @staticmethod
    def reports(calls, clear=False):
        return [(tick, args) for tick, name, args in calls if name == "herdr"
                and args[:2] == ["workspace", "report-metadata"]
                and (("--clear-token" in args) == clear)]

    def test_renewal_and_call_counts_against_baseline(self):
        path, env = self.fixture(polls=7, workspaces=4)
        calls = self.run_watch(path, env)
        self.assertEqual([t for t, a in self.reports(calls)][::4], [0, 2, 4, 6])
        for _, args in self.reports(calls):
            self.assertEqual(args[args.index("--ttl-ms") + 1], "25000")
        baseline = subprocess.check_output(["git", "show", BASELINE + ":herdr-ports"],
                                           cwd=ROOT, text=True)
        # Export definitions only; intercept baseline cleanup and terminate its
        # unbounded loop at the same mock sleep boundary as the new watcher.
        old_path, old_env = self.fixture(polls=7, workspaces=4)
        source = old_path / "baseline"
        source.write_text(baseline.rsplit('case "${1:-}" in', 1)[0])
        harness = '''source "$1"
watcher_alive() { return 1; }
pidfile_path() { printf '%s/pid' "$FIXTURE"; }
rm() { :; }
sleep() { command sleep "$@" || exit 0; }
watch_loop
'''
        subprocess.run(["/bin/bash", "-c", harness, "test", str(source)],
                       env=old_env, check=True, timeout=15, capture_output=True)
        def counts(items):
            return dict(collections.Counter("snapshot" if name == "herdr" and args[:1] == ["api"]
                       else "metadata" if name == "herdr" else name
                       for _, name, args in items if name in ("netstat", "lsof", "herdr")))
        before, after = counts(self.calls(old_path)), counts(calls)
        self.assertEqual(before, dict(netstat=7, lsof=7, snapshot=7, metadata=28))
        self.assertEqual(after, dict(netstat=7, lsof=7, snapshot=3, metadata=16))
        print("\nFixed 7 polls / 4 workspaces:", before, "->", after)
        # Perl execs /bin/bash by absolute path once; account for that launch
        # explicitly in addition to every intercepted PATH command.
        before_total, after_total = len(self.calls(old_path)), len(calls) + 1
        print("All external commands (including lock startup and Bash exec):",
              before_total, "->", after_total)
        self.assertLess(after_total, before_total)

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

    def test_workspace_cache_expires_for_pane_cd(self):
        path, env = self.fixture(polls=5, workspace_cwd=[[1, "/other"]])
        calls = self.run_watch(path, env)
        self.assertEqual([t for t, n, a in calls if n == "herdr" and a[:1] == ["api"]], [0, 3])
        self.assertEqual([t for t, _ in self.reports(calls, True)], [3, 3])

    def test_pid_cwd_is_never_cached(self):
        path, env = self.fixture(polls=3, pid_cwd=[[1, "/other"], [2, "/project"]])
        calls = self.run_watch(path, env)
        self.assertEqual([t for t, _ in self.reports(calls, True)], [1, 1])
        self.assertEqual([t for t, _ in self.reports(calls)], [0, 0, 2, 2])

    def test_snapshot_failure_preserves_badges_and_retries(self):
        for failure in ("snapshot_fail", "snapshot_invalid", "snapshot_empty_response"):
            path, env = self.fixture(polls=5, **{failure: [3]})
            calls = self.run_watch(path, env)
            self.assertEqual(self.reports(calls, True), [])
            self.assertEqual([t for t, n, a in calls if n == "herdr" and a[:1] == ["api"]], [0, 3, 4])
            self.assertEqual([t for t, _ in self.reports(calls)][-2:], [4, 4])

    def test_scan_and_cwd_failure_are_not_empty_sets(self):
        for failure in ("scan_fail", "cwd_fail"):
            path, env = self.fixture(polls=4, listeners=[[2, False]], **{failure: [1]})
            calls = self.run_watch(path, env)
            self.assertEqual([t for t, _ in self.reports(calls, True)], [2, 2])

    def test_lsof_partial_success_keeps_attribution_and_renewal(self):
        path, env = self.fixture(polls=5, cwd_partial=[0, 1, 2, 3, 4])
        calls = self.run_watch(path, env)
        self.assertEqual([t for t, _ in self.reports(calls)], [0, 0, 2, 2, 4, 4])
        self.assertEqual(self.reports(calls, True), [])

    def test_lsof_fallback_partial_empty_and_failure(self):
        path, env = self.fixture(polls=4, platform="Other", scan_partial=True,
                                 scan_fail=[1], listeners=[[2, False]])
        calls = self.run_watch(path, env)
        self.assertEqual([t for t, _ in self.reports(calls)], [0, 0])
        self.assertEqual([t for t, _ in self.reports(calls, True)], [2, 2])

    def test_attribution_rules_and_empty_workspace_file(self):
        path, env = self.fixture()
        workspace = path / "workspaces"
        for rows, cwd, expected in (
            ("w\t/project/src\n", "/project", "w"),
            ("w\t/project/src\n", "/project/src/sub", "w"),
            ("w\t/project/src\n", "/project/src", "w"),
            ("w\t/project/src\n", "/project/src-other", ""),
            ("w\t" + env["HOME"] + "/src\n", env["HOME"], ""),
            ("", "/project", ""),
        ):
            workspace.write_text(rows)
            result = subprocess.run(["/bin/bash", "-c", 'source "$1"; match_join "$2"',
                                     "test", str(SCRIPT), str(workspace)],
                                    env=env, input="424242\t" + cwd + "\n",
                                    text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip().split("\t")[-1], expected)

    def test_successful_empty_snapshot_clears(self):
        path, env = self.fixture(polls=4, workspace_cwd=[[3, "/"]])
        calls = self.run_watch(path, env)
        self.assertEqual([t for t, _ in self.reports(calls, True)], [3, 3])

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
        for key in ("HERDR_PORTS_INTERVAL", "HERDR_PORTS_WORKSPACE_INTERVAL"):
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
