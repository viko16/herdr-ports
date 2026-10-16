# Development

## Scope and compatibility

- This plugin provides sidebar metadata and a read-only `list` command. Do not
  reintroduce popup UI or process termination actions.
- Keep Bash 3.2 and Python 3.8 compatibility. Use Python's standard library only.
- Support macOS and Linux. Test platform-specific command output with fixtures;
  do not infer the fixture platform from the test host.
- Keep the plugin ID `viko16.herdr-ports`. The manifest is the version source.
- Commit independent features and fixes separately; keep version bumps separate.

## Behavioral requirements

- Attribute listeners only through live terminal shell ancestry. Cwd, pane names
  and command text must never establish ownership. Exclude plugin context and
  leave ambiguous or unreadable ownership unassigned.
- Keep terminal identity, process birth/parent checks and bounded shell caching.
- Distinguish a successful empty socket scan from failure. Failed scans preserve
  leases; failed metadata writes retry without advancing acknowledged state.
- Publish and clear `ports` and `portlist` together. Port changes must publish on
  the next successful poll, without waiting for lease renewal.
- Preserve per-socket kernel locking and direct session socket RPC. Do not add
  per-PID subprocesses, per-workspace CLI launches or cwd reads to the watcher.

## Validation

```sh
/bin/bash -n herdr-ports
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
git diff --check
```

Use isolated fixtures for Herdr, process tables, environments, time and socket
listings. Never inspect or stop the developer's real services in tests.
`SocketTests` binds private Unix sockets: report environment permission failures
explicitly rather than weakening the tests or claiming a full pass.

The stable polling test checks executable and RPC budgets as well as output.
Changing these budgets requires an explanation tied to actual added work.
Real macOS/Bash 3.2 verification is distinct from mocked tests on Linux.

## Documentation

Keep README focused on installation, configuration and user-visible behavior.
Put protocol, cache, locking and error-handling details beside the relevant code.
Keep development rules here and benchmark mechanics in tests, not in README.
