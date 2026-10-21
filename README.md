# herdr-ports

See which [Herdr](https://herdr.dev) Spaces have running services, right in the
sidebar: a `↯` badge and an optional port list such as `:3000 :5173`.

## Install

```sh
herdr plugin install viko16/herdr-ports
```

Requires Herdr 0.9.0+, Bash 3.2+, Python 3.8+ and Perl with its standard
`Fcntl` and `Digest::SHA` modules. On macOS, install Python if needed; the other
tools ship with the system. On Linux, also install `ss` (iproute2) or `lsof`.

## Configure

Add `$ports` and `$portlist` to your existing sidebar rows in Herdr's
`config.toml`:

```toml
[ui.sidebar.spaces]
rows = [["state_icon", "workspace", "$ports"], ["branch", "git_status", "$portlist"]]
```

Remove `$portlist` if you only want the badge. Then reload the configuration:

```sh
herdr server reload-config
```

Open a terminal pane to start the watcher automatically. No separate daemon
setup is needed.

## How the badge works

Every five seconds, the plugin checks TCP listeners and follows their parent
processes to find the Space that started them. Two Spaces using the same
directory stay separate. Ports are sorted and deduplicated; both the badge and
port list clear after the last service stops.

Services started outside Herdr and plugin helpers are excluded. Services that
detach from their terminal or clear Herdr's environment may not appear. If a
scan fails, the previous display is kept temporarily and eventually expires.

## Options

Set these environment variables before starting the watcher:

| Variable | Default | Purpose |
| --- | --- | --- |
| `HERDR_PORTS_INTERVAL` | `5` | Poll interval in whole seconds, from 1 to 3600, without leading zeros |
| `HERDR_PORTS_BADGE` | `↯` | Badge text; Nerd Font icons are not supported by Herdr metadata |

For a read-only table of listeners, run `./herdr-ports list` from the plugin
directory. Unassigned listeners show `-` in the Space column.

## Upgrading

A running watcher keeps using the code it started with. Finish updating the
plugin files before starting or restarting it; new behavior takes effect only
after a restart. A configuration reload or restarting Herdr alone does not
replace the watcher.

To restart manually:

1. Find the watcher with `ps -axo pid=,args= | grep '[h]erdr-ports-watch'`.
   Check its full plugin path and session before choosing a PID; older installs
   may have more than one watcher. Leave development servers running.
2. Run `kill -TERM <PID>` for each confirmed watcher you are replacing, and wait
   for it and its in-flight helpers to exit.
3. Open a new terminal pane in the same Herdr session, or run
   `./herdr-ports ensure-watch` from that session's terminal in the plugin
   directory.

A reboot followed by opening a Herdr terminal pane also loads the new code.
Watchers started before this upgrade need this one-time restart too.

The popup has been removed. Delete any old `[[keys.command]]` entry that invokes
`numbered.ports.open` or `viko16.herdr-ports.open`.
