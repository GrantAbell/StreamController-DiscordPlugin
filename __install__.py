import subprocess
from streamcontroller_plugin_tools.installation_helpers import create_venv
from os.path import join, abspath, dirname

toplevel = dirname(abspath(__file__))
create_venv(join(toplevel, ".venv"), join(
    toplevel, "assets", "requirements.txt"))

# Grant the StreamController Flatpak live access to the Discord app runtime
# directory so it can find the Discord IPC socket even when Discord starts
# after StreamController.
subprocess.run(
    [
        "flatpak", "override", "--user",
        "--filesystem=xdg-run/app/com.discordapp.Discord",
        "com.core447.StreamController",
    ],
    check=False,
)
