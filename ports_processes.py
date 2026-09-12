"""Attribute TCP listeners to live Herdr terminal shells; cwd is display only."""
import ctypes
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys


# Snapshot membership/terminal IDs and kernel birth/parent IDs are checked on
# every scan. This is only a fallback revalidation of the Herdr association.
SHELL_CACHE_SECONDS = 300


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)


def api(*args):
    if args == ("api", "snapshot"):
        method, params = "session.snapshot", {}
    elif len(args) == 4 and args[:3] == ("pane", "process-info", "--pane"):
        method, params = "pane.process_info", {"pane_id": args[3]}
    else:
        raise ValueError("unsupported read method")
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = os.environ.get("HERDR_SOCKET_PATH") or str(config / "herdr/herdr.sock")
    # Herdr 0.9.0 schema: newline-delimited Request/Response, protocol 22.
    # One connection per request keeps failures local; no persistent client.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(path)
        connection.sendall(json.dumps(dict(id="ports", method=method, params=params)).encode() + b"\n")
        with connection.makefile("rb") as response:
            data = json.loads(response.readline())
    if not isinstance(data, dict) or data.get("id") != "ports" or "error" in data or not isinstance(data.get("result"), dict):
        raise ValueError("invalid Herdr response")
    return data["result"]


def process_table():
    # One batch for ancestry and original command display, never ps per PID.
    rows = {}
    for line in command("ps", "-ww", "-axo", "pid=,ppid=,args=").splitlines():
        pid, parent, args = line.strip().split(None, 2)
        rows[int(pid)] = (int(parent), args)
    if not rows:
        raise ValueError("empty process table")
    return rows


def mac_environment(raw):
    """KERN_PROCARGS2: argc, executable path/padding, argc argv strings, env."""
    if len(raw) < 4:
        raise ValueError("missing argc")
    argc = struct.unpack_from("i", raw)[0]
    if argc < 1:
        raise ValueError("missing argv")
    offset = raw.index(b"\0", 4) + 1
    while offset < len(raw) and raw[offset] == 0:
        offset += 1
    for _ in range(argc):
        offset = raw.index(b"\0", offset) + 1
    return environment(raw[offset:])


def environment(raw):
    # Preserve the kernel's NUL boundaries; argv mentioning a plugin is not env.
    return dict(item.split(b"=", 1) for item in raw.split(b"\0") if b"=" in item)


class KernelProcesses:
    """Direct reads in this process; no per-PID subprocess or persisted env."""

    def __init__(self):
        if sys.platform == "darwin":
            self.libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            self.libc = ctypes.CDLL(None, use_errno=True)

    def identity(self, pid):
        if sys.platform == "darwin":
            # proc_bsdinfo, SDK sys/proc_info.h: 12 u32, 48 name bytes,
            # 6 u32, start timeval (two u64). Birth time includes microseconds.
            buf = ctypes.create_string_buffer(136)
            size = self.libproc.proc_pidinfo(pid, 3, 0, buf, len(buf))
            if size != len(buf):
                raise OSError("process identity unavailable")
            parent = struct.unpack_from("I", buf, 16)[0]
            birth = struct.unpack_from("QQ", buf, 120)
            return (parent, *birth)
        if sys.platform.startswith("linux"):
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return (int(fields[1]), int(fields[19]), 0)
        raise OSError("unsupported process identity platform")

    def environ(self, pid):
        if sys.platform == "darwin":
            # CTL_KERN/KERN_PROCARGS2 from SDK sys/sysctl.h.
            mib = (ctypes.c_int * 3)(1, 49, pid)
            size = ctypes.c_size_t(0)
            if self.libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0):
                raise OSError("process environment unavailable")
            buf = ctypes.create_string_buffer(size.value)
            if self.libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0):
                raise OSError("process environment unavailable")
            return mac_environment(buf.raw[:size.value])
        if sys.platform.startswith("linux"):
            return environment(Path(f"/proc/{pid}/environ").read_bytes())
        raise OSError("unsupported process environment platform")


def plugin_environment(env):
    # Herdr plugin invocation context, also inherited by ordinary children.
    return any(key in env for key in (b"HERDR_PLUGIN_ROOT", b"HERDR_PLUGIN_STATE_DIR"))


def pane_shells(snapshot, table, kernel, cache, now, read_api=api):
    panes = snapshot["panes"]
    if not isinstance(panes, list):
        raise ValueError("missing pane inventory")
    updated, roots = {}, {}
    for pane in panes:
        pane_id, terminal, workspace = (pane[key] for key in ("pane_id", "terminal_id", "workspace_id"))
        if not all(isinstance(value, str) and value for value in (pane_id, terminal, workspace)):
            raise ValueError("invalid pane identity")
        entry = cache.get(pane_id)
        valid = False
        if entry and entry["terminal"] == terminal:
            try:
                same_process = entry["pid"] in table and list(kernel.identity(entry["pid"])) == entry["identity"]
            except (OSError, ValueError):
                same_process = False
            if not same_process:
                # process-info has no birth ID. A reused PID must never become
                # a new shell for the same terminal just because the API still
                # reports that number. Retain this tombstone until pane closes
                # or terminal_id changes.
                updated[pane_id] = entry
                continue
        if entry and entry["terminal"] == terminal:
            valid = 0 <= now - entry["time"] < SHELL_CACHE_SECONDS
        if not valid:
            info = read_api("pane", "process-info", "--pane", pane_id)["process_info"]
            if info["pane_id"] != pane_id:
                raise ValueError("mismatched pane identity")
            pid = info.get("shell_pid")
            if not isinstance(pid, int) or pid <= 1 or pid not in table:
                if entry and entry["terminal"] == terminal:
                    updated[pane_id] = entry
                continue  # Unknown shell: no positive cache, retry next scan.
            try:
                identity = kernel.identity(pid)
            except (OSError, ValueError):
                continue
            if identity[0] != table[pid][0]:
                continue
            if entry and entry["terminal"] == terminal and (pid != entry["pid"] or list(identity) != entry["identity"]):
                updated[pane_id] = entry
                continue
            entry = dict(terminal=terminal, pid=pid, identity=list(identity), time=now)
        updated[pane_id] = entry
        pid = entry["pid"]
        # Duplicate claims are ambiguous, even when the labels/cwds agree.
        roots[pid] = None if pid in roots else (workspace, tuple(entry["identity"]))
    cache.clear()
    cache.update(updated)  # Closed panes disappear on this scan, not after TTL.
    return roots


def owners(listeners, table, roots, kernel):
    checked = {}

    def read(pid):
        if pid not in checked:
            try:
                identity = kernel.identity(pid)
                env = kernel.environ(pid)
                if identity != kernel.identity(pid) or identity[0] != table[pid][0]:
                    raise ValueError("process changed during read")
                checked[pid] = (identity, env)
            except (OSError, ValueError, KeyError):
                checked[pid] = None
        return checked[pid]

    result = {}
    for listener in listeners:
        pid, chain = listener, []
        while pid > 1 and pid in table and pid not in chain:
            chain.append(pid)
            if pid in roots:
                break
            pid = table[pid][0]
        root = roots.get(pid)
        if pid == listener or not root:
            continue  # External/broken trees need no environment reads at all.
        for member in chain:
            record = read(member)
            if record is None or plugin_environment(record[1]):
                break
            if member == pid:
                if record[0] != root[1]:
                    break
            elif record[1].get(b"HERDR_ENV") != b"1":
                # Stripped Herdr context cannot rule out stripped plugin context.
                break
        else:
            # Recheck every birth/parent at the end; no stale chain after exit,
            # reparenting or PID reuse during API/environment reads.
            try:
                if all(kernel.identity(p) == checked[p][0] for p in chain):
                    result[listener] = root[0]
            except (OSError, ValueError):
                pass
    return result


def scan(listeners, cache, now, read_api=api, read_table=process_table, kernel=None, unknown_on_error=False):
    if not listeners:
        cache.clear()
        return {}, {}, {"workspaces": []}
    table = {}
    try:
        table = read_table()
        snapshot = read_api("api", "snapshot")["snapshot"]
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("workspaces"), list):
            raise ValueError("invalid snapshot")
        kernel = kernel or KernelProcesses()
        roots = pane_shells(snapshot, table, kernel, cache, now, read_api)
        return owners(listeners, table, roots, kernel), table, snapshot
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        if not unknown_on_error:
            raise  # Watcher must preserve leases on a failed global read.
        return {}, table, {"workspaces": []}


def display_rows(ports, assigned, table, snapshot, cwds):
    labels = {w["workspace_id"]: w.get("label") or w["workspace_id"] for w in snapshot["workspaces"]}
    for pid in sorted(ports, key=lambda p: (assigned.get(p, "~"), min(ports[p]), p)):
        ws = assigned.get(pid)
        cells = [labels.get(ws, ws) if ws else "-", str(pid),
                 " ".join(f":{port}" for port in sorted(ports[pid])),
                 cwds.get(pid, "-"), table.get(pid, (0, "?"))[1][:60]]
        print("\t".join(cell.replace("\t", " ").replace("\n", " ") for cell in cells))


def main():
    mode, cache_path, timestamp, *files = sys.argv[1:]
    cache = json.loads(Path(cache_path).read_text()) if cache_path != "-" and Path(cache_path).exists() else {}
    ports = {}
    for line in sys.stdin:
        pid, port = map(int, line.split())
        ports.setdefault(pid, set()).add(port)
    assigned, table, snapshot = scan(ports, cache, float(timestamp), unknown_on_error=mode == "rows")
    if cache_path != "-":
        pending = Path(cache_path + ".new")
        pending.write_text(json.dumps(cache))
        pending.replace(cache_path)
    if mode == "active":
        print("\n".join(sorted(set(assigned.values()))))
    else:
        cwds = {}
        for line in Path(files[0]).read_text().splitlines():
            pid, cwd = line.split("\t", 1)
            cwds[int(pid)] = cwd
        display_rows(ports, assigned, table, snapshot, cwds)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # Never print argv, environment or raw API error payloads.
        sys.exit(1)
