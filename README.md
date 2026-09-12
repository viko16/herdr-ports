# herdr-ports

Show which Space started a listening service in [herdr](https://herdr.dev):
a generic `$ports` badge (`↯`) and a popup to inspect and stop TCP listeners.

A listener is assigned only when its live parent chain reaches a terminal
`shell_pid` reported by Herdr for that Space. For example,
`shell -> tnpm -> node` belongs to the shell's Space. Two Spaces using the same
directory remain separate. A service started outside Herdr, a reparented daemon,
or an incomplete/ambiguous process chain has no assignment. Cwd is display only.

The popup and badge use the same resolver. The default popup shows assigned
listeners; `all` includes unknown and plugin listeners with Space `-`.
The original command display is kept (first 60 characters), from the same
batch process table used for ancestry.

Plugin context is read from the actual process environment, with NUL-delimited
boundaries: `HERDR_PLUGIN_ROOT` or `HERDR_PLUGIN_STATE_DIR` on any process in
the chain excludes it. This also covers a plugin helper started under an ordinary
terminal shell. A normal command inheriting that context is conservatively
unassigned. Pane labels (including `Sidebar`) and command text are not identity.

Herdr 0.9.0 exposes no queryable plugin-pane inventory or birth identifier in
`process-info`. To avoid attributing processes that stripped plugin context,
each non-root process in the chain must retain `HERDR_ENV=1`. Consequently,
ordinary `env -i` services and commands that clear that environment are also
unassigned. Unreadable process identity/environment is unknown, never evidence
of ordinary-terminal ownership. No program names or ports are blacklisted.

## Install

```sh
herdr plugin install Numbered-com/herdr-ports
```

Requirements: Herdr 0.9.0+, **Python 3.8+** on `PATH` (standard library only),
plus the fastest available socket lister - `netstat`
(macOS, built-in), `ss` (Linux, iproute2) or `lsof` (universal fallback; also
used on macOS to resolve process cwds). Bash 3.2+ (stock macOS works).
The watcher also requires Perl with its core `Fcntl` and `Digest::SHA` modules
(included with macOS); no plugin installation is needed for development tests.

## Configure

Plugins cannot inject sidebar rows or keybindings (herdr plugin v1), so add to
your `config.toml`:

```toml
# Show the badge next to the Space title
[ui.sidebar.spaces]
rows = [["state_icon", "workspace", "$ports"], ["branch", "git_status"]]

# Open the popup with prefix+a
[[keys.command]]
key = "prefix+a"
type = "plugin_action"
command = "numbered.ports.open"
```

Then `herdr server reload-config`.

## Popup

```
      SPACE        PORTS          PID     PATH                           COMMAND
> [ ] webapp       :3000          48213   ~/dev/webapp                   next-server (v16)
  [x] api          :8080          48377   ~/dev/api                      bun run src/index.ts

  ↑↓ move · space check · a all · r refresh
                ↵ kill    esc close
```

The footer mimics herdr's native settings modal: a dim hint line (replaced by
the outcome of the last kill), then CTA chips - accent `↵ kill`, gray
`esc close`. Set `HERDR_PORTS_ACCENT` (256-color index, default 223) to match
your theme's accent. Hints and chips are all clickable. Mouse: click a row to
check it, use the wheel to move. Keyboard:

- arrows / `j` `k`: move - `space`: check - `enter`: kill checked rows (or the
  highlighted one when none checked); TERM, then the list redraws the instant
  the targets exit (stragglers get KILL in the background), and the Space's
  `$ports` badge clears right away instead of waiting out the metadata TTL
- `a`: include listeners outside herdr workspaces - `r`: refresh - `esc` / `q`: quit

## How the badge works

`herdr-ports watch` polls every 5s (`HERDR_PORTS_INTERVAL`) and posts a
`ports=↯` token (`HERDR_PORTS_BADGE`) as workspace metadata with a TTL,
so the badge clears itself shortly after the last server dies. Nerd Font
glyphs cannot be used here: herdr strips Private Use Area characters from
metadata tokens. The watcher is a singleton started automatically by a
`pane.created` event hook - no daemon setup needed.

For each nonempty listener scan, the resolver reads one batch process table and
one fresh snapshot. Herdr 0.9.0 requires one `pane process-info` call per pane
to obtain its shell PID; successful mappings are cached for at most 15 seconds.
The cache is private to that watcher and bounded by the current pane inventory.
Every scan checks the terminal ID and kernel process birth/parent identity
(macOS microsecond birth time; Linux start ticks). A closed pane is removed on
the next snapshot even if its shell is still alive. A changed terminal ID forces
a fresh lookup. A reused PID or changed shell PID cannot rebind the same terminal;
it stays unknown until a new terminal identity is observed. There is no cached
cwd or environment, and no per-PID external command.

Only processes on listener ancestry chains have their environments read, directly
through macOS sysctl/libproc or Linux procfs. Birth/parent identities are rechecked
after those reads. Empty listener scans clear the shell cache and skip snapshot,
process-info, process-table and environment reads. The next nonempty scan rebuilds
the mapping. These are point-in-time observations; changes during a scan can
remain unknown until the next scan.

The polling interval must be a whole number from 1 to 3600 (no leading zeros).
Badges are posted immediately on appearance and renewed every two polling
intervals, with a TTL of three intervals plus 10s (25s by default). Confirmed
loss of attribution clears on the next poll. Failed socket/table/API reads
preserve lease state without posting or clearing and retry on the next poll;
a prolonged outage falls back to TTL. An inaccessible individual process is
unassigned. The popup treats failed attribution reads as unknown and keeps the
socket rows available in its all view. A cwd read failure affects display only.

The singleton uses a kernel file lock keyed by user and `HERDR_SOCKET_PATH`
(default `${XDG_CONFIG_HOME:-$HOME/.config}/herdr/herdr.sock`). Different socket
paths have independent watchers. Lock files remain in `/tmp/herdr-ports-watch-UID`
to preserve inode identity; they contain no PID and need no stale-PID cleanup.
Exit/crash releases ownership when the last inherited file descriptor closes;
an in-flight child command retains it until that child exits. No process is signalled to
acquire a lock. Updating linked source does not replace functions already loaded
by a running watcher; its lifecycle must be handled separately.

## Development checks

```sh
/bin/bash -n herdr-ports
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Tests mock Herdr, sockets, process tables, kernel identity/environment, clock and
sleep in temporary fixtures. They do not create panes or inspect/stop real services.
Coverage includes shell/tnpm/node ancestry, same-directory Spaces, external
services, reparenting, plugin descendants and inherited context, ordinary Sidebar
labels, cleared environments, broken trees, PID reuse, terminal replacement,
immediate closure, cache refresh, empty scans, failures, singleton locking and TTL.

The fixed workload is seven polls with four stable active panes/Spaces. Its exact
budget is **96 external commands**, including seven Python helper launches,
seven socket dumps, seven `ps` calls, seven snapshots, twelve process-info calls
(three refreshes per pane), sixteen metadata writes, lock startup and the absolute
Bash exec. Snapshot/process-info costs are included, not hidden by mocking.
The watcher does not read cwds. The count excludes shell builtins, subshells,
in-process kernel/file reads and the test harness; it is not a CPU/time benchmark.
More panes add one process-info call each per refresh. The popup also makes one
batch cwd read on macOS and uses the same resolver without retaining shell mappings.

## CLI

```sh
herdr plugin action invoke open --plugin numbered.ports   # open the popup
./herdr-ports list                                        # one-shot table
./herdr-ports watch                                       # run the poller in foreground
```
