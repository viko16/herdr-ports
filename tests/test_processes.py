"""Isolated ancestry/API/kernel fixtures; never start or signal real services."""
import contextlib
import copy
import io
from pathlib import Path
import struct
import sys
import json
import os
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ports_processes as ports


class Kernel:
    def __init__(self, table):
        self.births = {pid: (parent, pid, 123) for pid, (parent, _) in table.items()}
        self.envs = {pid: {b"HERDR_ENV": b"1"} for pid in table}
        self.env_reads = []

    def identity(self, pid):
        if pid not in self.births:
            raise OSError("unavailable")
        return self.births[pid]

    def environ(self, pid):
        self.env_reads.append(pid)
        if self.envs[pid] is None:
            raise OSError("permission denied")
        return self.envs[pid]


class AncestryTests(unittest.TestCase):
    def setUp(self):
        self.table = {10: (1, "zsh"), 20: (10, "tnpm run serve"), 30: (20, "node app.js")}
        self.kernel = Kernel(self.table)
        self.panes = [dict(pane_id="p1", terminal_id="t1", workspace_id="w1", cwd="/shared", label="Sidebar")]
        self.shells = {"p1": 10}
        self.cache, self.api_calls = {}, []
        self.table_reads = 0

    def api(self, *args):
        self.api_calls.append(args)
        if args == ("api", "snapshot"):
            return {"snapshot": {"panes": copy.deepcopy(self.panes), "workspaces": []}}
        return {"process_info": {"pane_id": args[-1], "shell_pid": self.shells[args[-1]]}}

    def read_table(self):
        self.table_reads += 1
        return self.table

    def scan(self, now=100, listeners=(30,)):
        return ports.scan(listeners, self.cache, now, self.api, self.read_table, self.kernel)[0]

    def add(self, pid, parent, env=None):
        self.table[pid] = (parent, "service")
        self.kernel.births[pid] = (parent, pid, 123)
        self.kernel.envs[pid] = env if env is not None else {b"HERDR_ENV": b"1"}

    def test_shell_tnpm_node_and_normal_sidebar_label(self):
        self.assertEqual(self.scan(), {30: "w1"})
        self.assertEqual(self.kernel.env_reads, [30, 20, 10])

    def test_same_cwd_different_spaces_and_external_proxy(self):
        self.add(40, 1)
        self.add(50, 40)
        self.add(60, 1)  # external terminal service in the same /shared cwd
        self.panes.append(dict(self.panes[0], pane_id="p2", terminal_id="t2", workspace_id="w2"))
        self.shells["p2"] = 40
        self.assertEqual(self.scan(listeners=(30, 50, 60)), {30: "w1", 50: "w2"})
        self.assertNotIn(60, self.kernel.env_reads)

    def test_daemon_reparent_loses_ownership_next_scan(self):
        self.assertEqual(self.scan(), {30: "w1"})
        self.table[30] = (1, "node app.js")
        self.kernel.births[30] = (1, 30, 123)
        self.assertEqual(self.scan(105), {})

    def test_plugin_pane_and_plugin_child_of_normal_shell(self):
        for plugin_pid in (10, 20, 30):
            with self.subTest(pid=plugin_pid):
                self.kernel.envs[plugin_pid][b"HERDR_PLUGIN_STATE_DIR"] = b"/state/plugins/example"
                self.assertEqual(self.scan(), {})
                del self.kernel.envs[plugin_pid][b"HERDR_PLUGIN_STATE_DIR"]
        self.assertEqual(self.scan(), {30: "w1"})

    def test_inherited_plugin_context_is_conservatively_unknown(self):
        # An otherwise ordinary command still carries exact plugin context.
        self.kernel.envs[30][b"HERDR_PLUGIN_ROOT"] = b"/plugins/example"
        self.assertEqual(self.scan(), {})

    def test_command_text_and_pane_label_are_not_plugin_identity(self):
        self.table[20] = (10, "echo HERDR_PLUGIN_ROOT=/plugins/example")
        self.kernel.envs[20][b"HERDR_PLUGIN_USER_SETTING"] = b"1"
        self.assertEqual(self.scan(), {30: "w1"})

    def test_env_i_normal_descendant_is_documented_unknown(self):
        # env -i preserves ancestry but strips the provenance needed to rule
        # out stripped plugin context; this intentional false negative is public.
        self.kernel.envs[20] = {}
        self.assertEqual(self.scan(), {})

    def test_unreadable_environment_or_identity_never_attributes(self):
        self.kernel.envs[20] = None
        self.assertEqual(self.scan(), {})
        self.kernel.envs[20] = {b"HERDR_ENV": b"1"}
        del self.kernel.births[20]
        self.assertEqual(self.scan(), {})

    def test_missing_broken_cyclic_and_ambiguous_trees(self):
        for parent in (999, 20, 30):
            self.table[20] = (parent, "tnpm")
            self.kernel.births[20] = (parent, 20, 123)
            self.assertEqual(self.scan(), {})
        self.table[20] = (10, "tnpm")
        self.kernel.births[20] = (10, 20, 123)
        self.panes.append(dict(self.panes[0], pane_id="p2", workspace_id="w2"))
        self.shells["p2"] = 10
        self.assertEqual(self.scan(), {})

    def test_cache_refresh_budget_and_only_chain_environments(self):
        for pid in range(1000, 1200):
            self.add(pid, 1)
        for now in (100, 105, 110, 115, 120, 125, 130):
            self.assertEqual(self.scan(now), {30: "w1"})
        self.assertEqual(self.table_reads, 7)
        self.assertEqual(self.api_calls.count(("api", "snapshot")), 7)
        self.assertEqual(self.api_calls.count(("pane", "process-info", "--pane", "p1")), 1)
        self.assertEqual(len(self.kernel.env_reads), 21)
        self.assertEqual(set(self.kernel.env_reads), {10, 20, 30})
        self.assertEqual(self.scan(399), {30: "w1"})
        self.assertEqual(self.api_calls.count(("pane", "process-info", "--pane", "p1")), 1)
        self.assertEqual(self.scan(400), {30: "w1"})
        self.assertEqual(self.api_calls.count(("pane", "process-info", "--pane", "p1")), 2)

    def test_snapshot_close_removes_live_shell_immediately(self):
        self.assertEqual(self.scan(), {30: "w1"})
        self.panes = []  # OS processes remain alive but pane is no longer owned.
        self.assertEqual(self.scan(101), {})
        self.assertEqual(self.cache, {})

    def test_pid_reuse_cannot_rebind_same_terminal_even_after_refresh(self):
        self.assertEqual(self.scan(), {30: "w1"})
        self.kernel.births[10] = (1, 10, 999)  # same second, different microsecond
        self.assertEqual(self.scan(105), {})
        self.assertEqual(self.scan(400), {})
        self.panes[0]["terminal_id"] = "replacement"
        self.assertEqual(self.scan(401), {30: "w1"})

    def test_new_shell_pid_requires_new_terminal_identity(self):
        self.assertEqual(self.scan(), {30: "w1"})
        self.add(11, 1)
        self.shells["p1"] = 11
        self.table[20] = (11, "tnpm")
        self.kernel.births[20] = (11, 20, 123)
        self.assertEqual(self.scan(400), {})
        self.panes[0]["terminal_id"] = "replacement"
        self.assertEqual(self.scan(401), {30: "w1"})

    def test_empty_listeners_drop_cache_and_skip_all_reads(self):
        self.assertEqual(self.scan(), {30: "w1"})
        self.api_calls.clear()
        self.assertEqual(self.scan(105, ()), {})
        self.assertEqual(self.api_calls, [])
        self.assertEqual(self.cache, {})
        self.assertEqual(self.table_reads, 1)
        self.assertEqual(self.scan(110), {30: "w1"})
        self.assertIn(("pane", "process-info", "--pane", "p1"), self.api_calls)

    def test_missing_shell_retries_without_positive_cache(self):
        self.shells["p1"] = None
        self.assertEqual(self.scan(), {})
        self.assertEqual(self.cache, {})
        self.shells["p1"] = 10
        self.assertEqual(self.scan(105), {30: "w1"})

    def test_missing_refresh_does_not_forget_previous_birth(self):
        self.assertEqual(self.scan(), {30: "w1"})
        self.shells["p1"] = None
        self.assertEqual(self.scan(400), {})
        self.shells["p1"] = 10
        self.kernel.births[10] = (1, 10, 999)
        self.assertEqual(self.scan(405), {})

    def test_new_pane_and_terminal_change_query_before_fallback(self):
        self.assertEqual(self.scan(), {30: "w1"})
        self.add(40, 1)
        self.add(50, 40)
        self.panes.append(dict(self.panes[0], pane_id="p2", terminal_id="t2", workspace_id="w2"))
        self.shells["p2"] = 40
        self.assertEqual(self.scan(105, (30, 50)), {30: "w1", 50: "w2"})
        self.assertEqual(self.api_calls.count(("pane", "process-info", "--pane", "p1")), 1)
        self.assertEqual(self.api_calls.count(("pane", "process-info", "--pane", "p2")), 1)
        self.panes[0]["terminal_id"] = "replacement"
        self.assertEqual(self.scan(110), {30: "w1"})
        self.assertEqual(self.api_calls.count(("pane", "process-info", "--pane", "p1")), 2)

    def test_fallback_failure_retries_without_extending_cache(self):
        self.assertEqual(self.scan(), {30: "w1"})
        original = self.api
        def failed(*args):
            if args[:1] == ("pane",):
                raise OSError("unavailable")
            return original(*args)
        self.api = failed
        with self.assertRaises(OSError):
            self.scan(400)
        self.assertEqual(self.cache["p1"]["time"], 100)
        self.api = original
        self.assertEqual(self.scan(405), {30: "w1"})
        self.assertEqual(self.cache["p1"]["time"], 405)

    def test_parent_identity_change_stays_unknown_during_cache(self):
        self.assertEqual(self.scan(), {30: "w1"})
        self.kernel.births[10] = (2, 10, 123)
        self.table[10] = (2, "zsh")
        self.assertEqual(self.scan(105), {})

    def test_identity_read_failure_retries_during_cache(self):
        self.assertEqual(self.scan(), {30: "w1"})
        birth = self.kernel.births.pop(10)
        self.assertEqual(self.scan(105), {})
        self.kernel.births[10] = birth
        self.assertEqual(self.scan(110), {30: "w1"})

    def test_reparent_during_environment_read_is_unknown(self):
        original = self.kernel.environ
        def reparent(pid):
            env = original(pid)
            if pid == 20:
                self.kernel.births[30] = (1, 30, 123)
            return env
        self.kernel.environ = reparent
        self.assertEqual(self.scan(), {})

    def test_display_includes_unknown_and_preserves_unicode_command(self):
        command = "node /shared/服务.js " + "x" * 80
        self.table[30] = (20, command)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            ports.display_rows({30: {3000}}, {}, self.table, {"workspaces": []}, {30: "/shared"})
        self.assertEqual(output.getvalue(), "-\t30\t:3000\t/shared\t" + command[:60] + "\n")

    def test_failed_api_preserves_watcher_failure_but_popup_shows_unknown(self):
        def failed_api(*args):
            raise OSError("unavailable")
        with self.assertRaises(OSError):
            ports.scan((30,), {}, 100, failed_api, self.read_table, self.kernel)
        assigned, table, snapshot = ports.scan((30,), {}, 100, failed_api, self.read_table,
                                               self.kernel, unknown_on_error=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            ports.display_rows({30: {3000}}, assigned, table, snapshot, {})
        self.assertEqual(output.getvalue(), "-\t30\t:3000\t-\tnode app.js\n")


class KernelParsingTests(unittest.TestCase):
    def test_mac_argv_boundaries_and_environment(self):
        raw = struct.pack("i", 3) + b"/bin/node\0\0\0node\0HERDR_PLUGIN_ROOT=argument\0server.js\0HERDR_ENV=1\0OTHER=secret\0\0"
        self.assertEqual(ports.mac_environment(raw), {b"HERDR_ENV": b"1", b"OTHER": b"secret"})
        self.assertFalse(ports.plugin_environment(ports.mac_environment(raw)))
        with self.assertRaises((ValueError, struct.error)):
            ports.mac_environment(raw[:10])

    def test_linux_stat_handles_spaces_and_parentheses_in_name(self):
        stat = "30 (some (process) name) S 20 " + "0 " * 17 + "12345 0"
        with patch.object(ports.sys, "platform", "linux"), patch.object(Path, "read_text", return_value=stat):
            self.assertEqual(ports.KernelProcesses().identity(30), (20, 12345, 0))


class SocketTests(unittest.TestCase):
    @contextlib.contextmanager
    def server(self, chunks):
        path = str(Path(tempfile.mkdtemp(prefix="ports-socket-")) / "session.sock")
        requests, errors, stop = [], [], threading.Event()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(path)
            server.listen(1)
            server.settimeout(5)
            def respond():
                try:
                    with server.accept()[0] as connection:
                        with connection.makefile("rb") as stream:
                            requests.append(json.loads(stream.readline()))
                        if chunks is None:
                            stop.wait(4)  # Exercise the client's real 3s timeout.
                        else:
                            for chunk in chunks:
                                connection.sendall(chunk)
                except OSError as error:
                    errors.append(error)
            thread = threading.Thread(target=respond, daemon=True)
            thread.start()
            try:
                yield path, requests
            finally:
                stop.set()
                thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])

    def test_segmented_response_and_exact_read_requests(self):
        for args, method, params in (
            (("api", "snapshot"), "session.snapshot", {}),
            (("pane", "process-info", "--pane", "w1:p2"), "pane.process_info", {"pane_id": "w1:p2"}),
        ):
            with self.server([b'{"id":"ports",', b'"result":', b'{"value":1}}', b'\n']) as (path, requests):
                with patch.dict(os.environ, HERDR_SOCKET_PATH=path):
                    self.assertEqual(ports.api(*args), {"value": 1})
                self.assertEqual(requests, [dict(id="ports", method=method, params=params)])

    def test_error_empty_mismatched_and_truncated_responses(self):
        for response in (
            b'{"id":"ports","error":{"code":"failed"}}\n',
            b'', b'{"id":"other","result":{}}\n', b'{"id":"ports"',
        ):
            with self.server([response]) as (path, _):
                with patch.dict(os.environ, HERDR_SOCKET_PATH=path), self.assertRaises((ValueError, OSError)):
                    ports.api("api", "snapshot")

    def test_timeout_and_connection_failure(self):
        with self.server(None) as (path, _):
            with patch.dict(os.environ, HERDR_SOCKET_PATH=path), self.assertRaises(socket.timeout):
                ports.api("api", "snapshot")
        with patch.dict(os.environ, HERDR_SOCKET_PATH=path), self.assertRaises(OSError):
            ports.api("api", "snapshot")

    def test_socket_path_isolation(self):
        for value in (1, 2):
            response = json.dumps(dict(id="ports", result=dict(session=value))).encode() + b'\n'
            with self.server([response]) as (path, _):
                with patch.dict(os.environ, HERDR_SOCKET_PATH=path):
                    self.assertEqual(ports.api("api", "snapshot"), {"session": value})

    def test_only_two_read_methods_are_supported(self):
        with patch.object(ports.socket, "socket", side_effect=AssertionError("must not connect")):
            for args in (("workspace", "report-metadata", "w1"), ("pane", "close", "w1:p1"), ("api", "unknown")):
                with self.assertRaises(ValueError):
                    ports.api(*args)


if __name__ == "__main__":
    unittest.main()
