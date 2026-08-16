import subprocess
from streamcontroller_plugin_tools.installation_helpers import create_venv
from os.path import join, abspath, dirname

toplevel = dirname(abspath(__file__))
create_venv(join(toplevel, ".venv"), join(
    toplevel, "assets", "requirements.txt"))

# Grant the StreamController Flatpak live access to the Discord app runtime
# directory so it can find the Discord IPC socket even when Discord starts
# after StreamController.
#
# The `:create` suffix is what makes "even when Discord starts after
# StreamController" actually hold. Flatpak resolves this grant once, while the
# sandbox is built: without `:create` it skips the mount whenever the directory
# does not exist yet, and the socket then stays invisible for the whole life of
# the process. With `:create` flatpak makes the directory itself before binding
# it, so Discord's socket shows up live the moment Discord creates it.
# Keep in sync with DISCORD_RUNTIME_GRANT in discordrpc/flatpak.py, which this
# script cannot import because it runs before the plugin venv exists.
#
# This script runs *inside* the StreamController Flatpak sandbox, where there is
# no `flatpak` binary and no way to modify host config directly. Route the
# command through `flatpak-spawn --host` so it runs on the host, and never let a
# failure here abort the (already-completed) venv install: it's a best-effort
# convenience, and `flatpak-spawn` is absent when running outside a sandbox.
try:
    subprocess.run(
        [
            "flatpak-spawn", "--host",
            "flatpak", "override", "--user",
            "--filesystem=xdg-run/app/com.discordapp.Discord:create",
            "com.core447.StreamController",
        ],
        check=False,
        timeout=30,
    )
except (FileNotFoundError, subprocess.SubprocessError):
    pass
