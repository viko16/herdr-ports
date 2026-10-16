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

Restart the existing watcher after updating (or reboot); a configuration reload
alone does not reload its code.

The popup has been removed. Delete any old `[[keys.command]]` entry that invokes
`numbered.ports.open` or `viko16.herdr-ports.open`.
