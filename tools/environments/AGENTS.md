# Terminal Environments — Development Guide

The `tools/environments/` directory implements execution backends for the terminal tool. Each backend spawns shell commands in a different environment (local machine, Docker container, SSH remote, cloud sandbox, etc.) behind a unified interface.

## Class Hierarchy

```
BaseEnvironment (ABC)         [base.py]
├── LocalEnvironment          [local.py]
├── DockerEnvironment         [docker.py]
├── SSHEnvironment            [ssh.py]
├── SingularityEnvironment    [singularity.py]
├── ModalEnvironment          [modal.py]
├── DaytonaEnvironment        [daytona.py]
└── BaseModalExecutionEnvironment  [modal_utils.py]
    └── ManagedModalEnvironment    [managed_modal.py]
```

`ProcessHandle` is a Protocol in `base.py`. `subprocess.Popen` satisfies it natively (local, docker, ssh, singularity). `_ThreadedProcessHandle` wraps SDK async calls for Modal and Daytona.

## Core Interface

**Abstract — each backend implements:**
- `_run_bash(cmd_string, *, login, timeout, stdin_data) -> ProcessHandle` — spawn bash in the target environment

**Provided by `BaseEnvironment` (not typically overridden):**
- `execute(command, cwd, *, timeout, stdin_data) -> dict` — public entry point. Calls `_before_execute()` → `_prepare_command()` (sudo) → `_wrap_command()` (snapshot sourcing + CWD markers) → `_run_bash()` → `_wait_for_process()` → `_update_cwd()`
- `init_session()` — runs login bash to capture `export -p`, function declarations, aliases into snapshot file. Sets `_snapshot_ready = True`
- `cleanup()` — release backend resources (containers, connections, sandboxes)

**Exception:** `ManagedModalEnvironment` bypasses `execute()` entirely — it polls a gateway HTTP API instead.

## Backend Selection

`TERMINAL_ENV` env var (default: `"local"`) drives `_create_environment()` factory in `terminal_tool.py` (~line 690). Called lazily on first use per `task_id`. Environments are cached in `_active_environments` and reaped after 300s of inactivity.

| Value | Backend | Image Config Env Var |
|-------|---------|---------------------|
| `local` | `LocalEnvironment` | — |
| `docker` | `DockerEnvironment` | `TERMINAL_DOCKER_IMAGE` |
| `ssh` | `SSHEnvironment` | `TERMINAL_SSH_HOST`, `_USER`, `_PORT`, `_KEY_PATH` |
| `singularity` | `SingularityEnvironment` | `TERMINAL_SINGULARITY_IMAGE` |
| `modal` | `ModalEnvironment` or `ManagedModalEnvironment` | `TERMINAL_MODAL_IMAGE` |
| `daytona` | `DaytonaEnvironment` | `TERMINAL_DAYTONA_IMAGE` |

Modal sub-selection: `TERMINAL_MODAL_MODE` (`auto`/`direct`/`managed`) + credential checks determine which Modal backend.

## Stdin Modes

| Mode | Backends | Mechanism |
|------|----------|-----------|
| `"pipe"` | local, docker, ssh, singularity | `subprocess.Popen` with `stdin=PIPE` |
| `"heredoc"` | modal, daytona | Stdin embedded as shell heredoc (SDK can't pipe) |
| `"payload"` | managed modal | Stdin sent as JSON field to gateway API |

## FileSyncManager (`file_sync.py`)

Used by SSH, Modal (direct), and Daytona — NOT by Docker/Singularity/Local (those use bind mounts or native FS).

- Instantiated with `get_files_fn` (returns host↔remote path pairs), `upload_fn`/`bulk_upload_fn`, `delete_fn`
- Rate-limited to once per 5 seconds unless `force=True`
- Transports: SSH uses `tar -c | ssh tar -x`; Modal uses `base64 | tar xzf -`; Daytona uses SDK multipart POST
- State rolls back on failure

Called via `_before_execute()` override in remote backends.

## Persistence Patterns

| Backend | Persistence | Mechanism |
|---------|-------------|-----------|
| Local | Native filesystem | — |
| Docker | Bind-mounts | `/workspace` + `/root` from `{HERMES_HOME}/sandboxes/docker/{task_id}/` |
| SSH | Stateless | Session snapshot is per-process only |
| Singularity | Writable overlay | `{scratch}/hermes-overlays/overlay-{task_id}`, tracked in `singularity_snapshots.json` |
| Modal | Filesystem snapshots | `sandbox.snapshot_filesystem()` on cleanup, restored at next init |
| Daytona | Workspace persistence | `sandbox.stop()` on cleanup, `sandbox.start()` on next init |

## Pitfalls

**Spawn-per-call model.** No persistent shell process. Every `execute()` spawns a fresh `bash -c`. Session state (env vars, aliases) persists by re-sourcing the snapshot file each call. Background jobs from one call won't survive to the next.

**CWD tracking is in-band for remote backends.** `__HERMES_CWD_{session}__` markers are embedded in stdout and stripped after parsing. `LocalEnvironment` reads a temp file instead. If a command outputs binary data containing the marker string, CWD extraction may misfire.

**`_snapshot_ready` fallback.** If `init_session()` fails (cold-start timeout, network issue), `_snapshot_ready = False` and subsequent commands run `bash -l` (login shell) instead of sourcing the snapshot. Env vars from earlier commands are lost.

**Secret stripping in Local.** `LocalEnvironment._make_run_env()` strips a large blocklist of Hermes API keys from the subprocess environment. The `_HERMES_FORCE_` prefix allows intentional passthrough. Docker has a separate `_forward_env` mechanism (opt-in).

**SSH ControlMaster socket.** Lives at `/tmp/hermes-ssh/{user}@{host}:{port}.sock` with `ControlPersist=300`. If process crashes without `cleanup()`, socket lingers. New instances auto-reuse it (usually harmless).

**Docker security flags.** `_SECURITY_ARGS` (cap-drop ALL, no-new-privileges, pids-limit 256) are hardcoded. `--storage-opt size=N` is probed at init and silently skipped on non-XFS overlay2 (macOS always skips).
