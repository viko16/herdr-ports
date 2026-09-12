# herdr-ports

Surface active dev servers in [herdr](https://herdr.dev): a generic `$ports`
badge on every Space running at least one TCP listener, and a popup to inspect
and kill them. Working on several projects at once, it answers "which space
has a live server?" at a glance.

Ports are attributed to a Space by matching the listener's process cwd against
the cwds of the workspace's panes - servers started outside herdr still show
up as long as they run inside a workspace directory. System daemons (cwd `/`,
`~/Library`, ...) never match, so the default view stays clean.

## Install

```sh
herdr plugin install Numbered-com/herdr-ports
```

Requirements: `jq`, plus the fastest available socket lister - `netstat`
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

Listeners and their process cwds are read on every poll. Pane/workspace cwds
are refreshed every 15s (`HERDR_PORTS_WORKSPACE_INTERVAL`), so a pane `cd` can
take up to that refresh period plus polling overhead to change attribution.
Both intervals must be whole seconds from 1 to 3600 (no leading zeros).
Badges are posted immediately on appearance and renewed every two polling
intervals, with a TTL of three intervals plus 10s (25s by default). A confirmed
disappearance clears the badge on the next poll. Failed reads preserve lease
state without posting or clearing; failed reads and metadata writes retry on
the next poll. During a prolonged outage the TTL clears badges naturally.

The singleton uses a kernel file lock keyed by user and `HERDR_SOCKET_PATH`
(default `${XDG_CONFIG_HOME:-$HOME/.config}/herdr/herdr.sock`). Different socket
paths have independent watchers. Lock files remain in `/tmp/herdr-ports-watch-UID`
to preserve inode identity; they contain no PID and need no stale-PID cleanup.
Exit/crash releases ownership when the last inherited file descriptor closes;
an in-flight child command retains it until that child exits. No process is signalled to
acquire a lock. An already running older watcher must exit separately before
using this version: its legacy PID file is deliberately not touched.

## Development checks

```sh
/bin/bash -n herdr-ports
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Tests use temporary fixtures and mock Herdr, socket listing, process cwd, clock
and sleep commands. They do not enable plugins or inspect/stop real services.
The fixed workload compares against commit
`bada711cac11c2243600019446c8a68216415569`: seven polls, four stable active
workspaces. It counts external commands invoked through PATH plus the lock
launcher's absolute `/bin/bash` exec, including lock startup, text processing,
snapshot and metadata calls; shell builtins, subshells and the test harness
are excluded. This measures command launches,
not elapsed time or CPU usage. Batch `lsof` partial success (exit 1 with valid
PID/cwd records) remains usable and is covered separately.

## CLI

```sh
herdr plugin action invoke open --plugin numbered.ports   # open the popup
./herdr-ports list                                        # one-shot table
./herdr-ports watch                                       # run the poller in foreground
```
