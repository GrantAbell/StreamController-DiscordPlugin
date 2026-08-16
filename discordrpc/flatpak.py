"""Helpers for the Flatpak sandbox StreamController normally runs inside.

Discord's IPC socket only ever exists inside Discord's own Flatpak runtime
directory ($XDG_RUNTIME_DIR/app/com.discordapp.Discord); the familiar
$XDG_RUNTIME_DIR/discord-ipc-0 is just a symlink into it. For our sandbox to
reach either one, flatpak has to bind-mount that directory when our sandbox is
built, and the grant has to carry the `:create` suffix.

Without `:create`, flatpak silently skips the mount whenever the directory does
not exist yet -- i.e. whenever StreamController wins the login race against
Discord -- and the socket then stays unreachable for the entire lifetime of the
StreamController process, no matter how often the watchdog retries.
"""

import os
import subprocess

from loguru import logger as log

DISCORD_FLATPAK_ID = "com.discordapp.Discord"
STREAMCONTROLLER_FLATPAK_ID = "com.core447.StreamController"

# Must stay in sync with the grant __install__.py applies; that script cannot
# import this module because it runs before the plugin venv exists.
DISCORD_RUNTIME_GRANT = f"--filesystem=xdg-run/app/{DISCORD_FLATPAK_ID}:create"

_repair_attempted = False


def in_sandbox() -> bool:
    """True when this process is running inside a Flatpak sandbox."""
    return os.path.exists("/.flatpak-info")


def diagnose_unreachable_socket(runtime_dir: str):
    """Explain, and permanently repair, an *unreachable* Discord IPC socket.

    Called once no discord-ipc-* socket could be connected to. If Discord's
    runtime directory is missing from our sandbox the socket is not merely
    absent, it is invisible, and retrying can never succeed -- so re-apply the
    override on the host and tell the user a restart is needed. Every other
    cause (Discord simply not running) stays quiet so the watchdog can keep
    polling.
    """
    global _repair_attempted
    if _repair_attempted or not in_sandbox():
        return

    discord_runtime_dir = os.path.join(runtime_dir, "app", DISCORD_FLATPAK_ID)
    if os.path.isdir(discord_runtime_dir):
        # The bind mount is in place, so Discord just isn't running yet.
        return
    _repair_attempted = True

    log.warning(
        f"{discord_runtime_dir} is not visible inside the StreamController "
        "Flatpak sandbox, so Discord cannot be detected even while it is "
        "running. This happens when StreamController starts before Discord. "
        "Re-applying the sandbox permission on the host -- restart "
        "StreamController for it to take effect."
    )
    try:
        result = subprocess.run(
            [
                "flatpak-spawn", "--host",
                "flatpak", "override", "--user",
                DISCORD_RUNTIME_GRANT,
                STREAMCONTROLLER_FLATPAK_ID,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as ex:
        log.warning(f"failed to re-apply the sandbox permission: {ex}")
        return
    if result.returncode != 0:
        log.warning(
            "failed to re-apply the sandbox permission "
            f"({result.returncode}): {result.stderr.strip()}"
        )
