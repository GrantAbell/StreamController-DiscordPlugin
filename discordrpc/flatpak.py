"""Helpers for the Flatpak sandbox StreamController normally runs inside.

Discord's IPC socket only ever exists inside Discord's own Flatpak sandbox.
What the rest of the world connects to is a copy Discord's launcher publishes on
the host, in Discord's per-app runtime directory
($XDG_RUNTIME_DIR/app/com.discordapp.Discord); the familiar
$XDG_RUNTIME_DIR/discord-ipc-0 is just a symlink into it.

Two separate things break that chain, and this module repairs both:

1. The directory is never mounted into our sandbox. Flatpak resolves the grant
   once, while our sandbox is built, and without a `:create` suffix it silently
   skips the mount whenever the directory does not exist yet -- i.e. whenever
   StreamController wins the login race against Discord. The socket is then
   unreachable for the entire lifetime of the StreamController process, no
   matter how often the watchdog retries.

2. The socket is mounted and visible, but Discord's launcher has deleted it.
   The launcher ties the published socket to a single `flatpak run` invocation
   rather than to Discord itself, so launching Discord again while it is
   already running -- clicking the launcher to raise the window is enough --
   can tear the socket down on the way out. Discord keeps running with nothing
   listening on its behalf, and only a full restart of Discord brings it back.
   See ipc_relay.py, which republishes it for as long as Discord lives.
"""

import os
import subprocess
import time

from loguru import logger as log

DISCORD_FLATPAK_ID = "com.discordapp.Discord"
STREAMCONTROLLER_FLATPAK_ID = "com.core447.StreamController"

# Must stay in sync with the grant __install__.py applies; that script cannot
# import this module because it runs before the plugin venv exists.
DISCORD_RUNTIME_GRANT = f"--filesystem=xdg-run/app/{DISCORD_FLATPAK_ID}:create"

RELAY_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ipc_relay.py")
# Publish the relay next to Discord's own socket rather than on top of it.
# discord-ipc-0 belongs to Discord's launcher, which deletes and recreates it on
# its own schedule -- racing it there is the very bug being repaired. sockets.py
# scans discord-ipc-0 through -9, so the plugin still finds this one, and
# Discord's own socket keeps priority for as long as it is healthy.
RELAY_SOCKET_INDEX = 1
# Only the host can tell whether Discord is running: Flatpak gives us no view of
# host processes, and hides Discord's own ~/.var/app directory from us even with
# filesystem=home. So asking costs a short-lived host process, and the answer
# sets how long to wait before asking again -- briskly while there is something
# to repair, sparingly while Discord is simply closed, which is most of the time.
RELAY_RETRY_INTERVAL = 15.0  # seconds
RELAY_IDLE_INTERVAL = 120.0  # seconds, once the host confirms Discord is closed
RELAY_EXIT_NO_DISCORD = 2  # ipc_relay.py's "Discord is not running" status

_permission_repair_attempted = False
_relay_attempted_at = 0.0
_relay_retry_interval = RELAY_RETRY_INTERVAL
_relay_failure_reported = False


def in_sandbox() -> bool:
    """True when this process is running inside a Flatpak sandbox."""
    return os.path.exists("/.flatpak-info")


def _run_on_host(argv: list):
    """Run a command outside our sandbox, or return None if we cannot."""
    try:
        return subprocess.run(
            ["flatpak-spawn", "--host", *argv],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            # flatpak-spawn hands the portal our working directory verbatim and
            # the host chdir()s into it, so it has to be a path that exists on
            # both sides. Ours is /app/bin/StreamController, which does not.
            cwd="/",
        )
    except (FileNotFoundError, subprocess.SubprocessError) as ex:
        log.warning(f"could not run {argv[0]} on the host: {ex}")
        return None


def diagnose_unreachable_socket(runtime_dir: str):
    """Explain, and where possible repair, an unreachable Discord IPC socket.

    Called once no discord-ipc-* socket could be connected to. Discord simply
    being closed stays quiet, so the watchdog can keep polling; the two ways the
    socket becomes *unreachable while Discord runs* are repaired here.
    """
    if not in_sandbox():
        return

    discord_runtime_dir = os.path.join(runtime_dir, "app", DISCORD_FLATPAK_ID)
    if not os.path.isdir(discord_runtime_dir):
        _reapply_sandbox_permission(discord_runtime_dir)
        return
    _restore_relay(runtime_dir)


def note_socket_connected():
    """Called on every successful connect, to reset the repair backoff.

    A working socket is proof Discord is up, which makes the slow "Discord is
    closed" cadence wrong: if this connection later breaks the socket should be
    republished promptly, not up to RELAY_IDLE_INTERVAL later.
    """
    global _relay_attempted_at, _relay_retry_interval
    _relay_attempted_at = 0.0
    _relay_retry_interval = RELAY_RETRY_INTERVAL


def _reapply_sandbox_permission(discord_runtime_dir: str):
    """Re-grant access to Discord's runtime directory, for the next start.

    A sandbox's mount namespace is frozen at creation, so this cannot help the
    running process -- but it makes the grant permanent, so the next start of
    StreamController sees the directory whether or not Discord is up yet.
    """
    global _permission_repair_attempted
    if _permission_repair_attempted:
        return
    _permission_repair_attempted = True

    log.warning(
        f"{discord_runtime_dir} is not visible inside the StreamController "
        "Flatpak sandbox, so Discord cannot be detected even while it is "
        "running. This happens when StreamController starts before Discord. "
        "Re-applying the sandbox permission on the host -- restart "
        "StreamController for it to take effect."
    )
    result = _run_on_host(
        [
            "flatpak", "override", "--user",
            DISCORD_RUNTIME_GRANT,
            STREAMCONTROLLER_FLATPAK_ID,
        ]
    )
    if result is not None and result.returncode != 0:
        log.warning(
            "failed to re-apply the sandbox permission "
            f"({result.returncode}): {result.stderr.strip()}"
        )


def _restore_relay(runtime_dir: str):
    """Republish Discord's IPC socket after its launcher has torn it down.

    Discord's directory is mounted and empty. Either Discord is closed, which is
    ordinary and silent, or Discord is running and its launcher deleted the
    socket -- in which case nothing will recreate it until Discord is fully
    restarted, so the plugin publishes its own relay instead.
    """
    global _relay_attempted_at, _relay_retry_interval, _relay_failure_reported

    now = time.monotonic()
    if _relay_attempted_at and now - _relay_attempted_at < _relay_retry_interval:
        return
    _relay_attempted_at = now

    listen_path = os.path.join(
        runtime_dir, "app", DISCORD_FLATPAK_ID, f"discord-ipc-{RELAY_SOCKET_INDEX}"
    )
    result = _run_on_host(
        [
            "python3", RELAY_SCRIPT,
            "--listen", listen_path,
            "--runtime-dir", runtime_dir,
        ]
    )

    if result is not None and result.returncode == RELAY_EXIT_NO_DISCORD:
        _relay_retry_interval = RELAY_IDLE_INTERVAL
        return  # Discord is closed; there was nothing to repair.

    _relay_retry_interval = RELAY_RETRY_INTERVAL
    if result is not None and result.returncode == 0:
        _relay_failure_reported = False
        log.info(
            "Discord was running without an IPC socket -- its Flatpak launcher "
            "removes the socket when Discord is launched a second time. "
            f"Republished it at {listen_path}; reconnecting."
        )
        return

    detail = "flatpak-spawn is unavailable"
    if result is not None:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
    if _relay_failure_reported:
        log.debug(f"could not republish Discord's IPC socket: {detail}")
        return
    _relay_failure_reported = True
    log.warning(f"could not republish Discord's IPC socket: {detail}")
