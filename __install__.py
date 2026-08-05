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
            "--filesystem=xdg-run/app/com.discordapp.Discord",
            "com.core447.StreamController",
        ],
        check=False,
        timeout=30,
    )
except (FileNotFoundError, subprocess.SubprocessError):
    pass
