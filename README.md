> [!NOTE] Fork of [ImDevinC](https://github.com/ImDevinC/)'s original archived plugin, with some enrichment to the existing actions.
> Only Official Discord Client Supported. 

> [!WARNING]
> Vesktop **will not** work with this plugin. This is an issue with Vesktop, and not something I can fix
> in this plugin.

## Expected flow
1. Create a Discord app at https://discord.com/developers/applications
1. Get a client ID and Client Secret
1. Make sure to set a `redirect url` of `http://localhost:9000` in the Oauth2 settings page
1. Use the Client ID and Client Secret on any of the actions (applying to one action will apply to all)

## Available Actions

- **Mute** - Toggle, enable, or disable microphone mute with visual feedback
- **Deafen** - Toggle deafen state to prevent hearing others in voice channels
- **TogglePTT** - Switch between Push-to-Talk and Voice Activity input modes
- **ChangeTextChannel** - Navigate to a specific text channel by ID
- **ChangeVoiceChannel** - Join or leave a specific voice channel by ID
- **UserVolume** - Control per-user volume levels via dial (rotate to adjust volume, press to cycle users)

## Setup

The plugin reaches Discord through a Unix socket. Where that socket lives depends
on how Discord was installed, and whether StreamController runs in a Flatpak
sandbox decides whether it can see it.

| | Discord as Flatpak | Discord native |
|---|---|---|
| **StreamController as Flatpak** | nothing to do | one Flatseal grant |
| **StreamController native** | nothing to do | nothing to do |

### StreamController outside Flatpak

Nothing to configure either way. There is no sandbox in the way, and the plugin
looks in both places Discord may have put the socket:
`$XDG_RUNTIME_DIR/discord-ipc-0` through `-9`, and the same names under
`$XDG_RUNTIME_DIR/app/com.discordapp.Discord/`.

### Flatpak StreamController + Flatpak Discord

Nothing to configure. Flatpak Discord keeps its socket inside its own sandbox and
publishes a copy in `$XDG_RUNTIME_DIR/app/com.discordapp.Discord/`, and the
plugin's installer grants StreamController access to that directory for you. To
reapply it by hand:

```bash
flatpak override --user --filesystem=xdg-run/app/com.discordapp.Discord:create com.core447.StreamController
```

Keep the `:create` suffix. Flatpak resolves the grant once, while the sandbox is
built, and without `:create` it silently skips the mount whenever the directory
does not exist yet — which is what happens when StreamController starts before
Discord. The socket then stays invisible for the whole life of the
StreamController process, however long it retries.

Because this grants a *directory*, sockets appearing in it later show up live, so
Discord can start, stop and restart freely underneath a running StreamController.

Earlier versions of this README also told you to grant Discord itself
`xdg-run/discord:create`. That is not needed — Discord creates its socket inside
its own sandbox and publishes it regardless — and it is safe to remove.

### Flatpak StreamController + native Discord

A native Discord puts its socket straight on the host at
`$XDG_RUNTIME_DIR/discord-ipc-0`, which a sandboxed StreamController cannot see by
default. Add `xdg-run/discord-ipc-0` in Flatseal:

![Flatseal](/content/flatseal.png)

or equivalently:

```bash
flatpak override --user --filesystem=xdg-run/discord-ipc-0 com.core447.StreamController
```

This grant names a single file rather than a directory, which brings two
limitations worth knowing before you hit them:

- **Start Discord before StreamController.** The grant is resolved once, when the
  sandbox is built. If the socket does not exist at that moment Flatpak skips it
  silently, and no amount of retrying will find it — only restarting
  StreamController will.
- **Restart StreamController after restarting Discord.** A file grant binds that
  exact file. Discord deletes and recreates its socket on restart, and the
  sandbox keeps pointing at the old, dead one until StreamController restarts.

Do **not** add `:create` to this grant. On a socket path `:create` makes a
*directory* named `discord-ipc-0`, which then stops Discord creating its socket
at all.

### Optional: publish the socket at the conventional path

Flatpak Discord keeps its socket in its own runtime directory rather than at
`$XDG_RUNTIME_DIR/discord-ipc-0`, where the Discord RPC convention puts it. This
plugin checks both, so it does not need the symlink below — but other Rich
Presence integrations that only check the conventional path do:

```bash
mkdir -p ~/.config/user-tmpfiles.d
echo 'L %t/discord-ipc-0 - - - - app/com.discordapp.Discord/discord-ipc-0' > ~/.config/user-tmpfiles.d/discord-rpc.conf
systemctl --user enable --now systemd-tmpfiles-setup.service
```

## Troubleshooting
This plugin uses sockets to communicate with Discord. A native Discord creates that
socket as `$XDG_RUNTIME_DIR/discord-ipc-0`; a Flatpak Discord publishes it as
`$XDG_RUNTIME_DIR/app/com.discordapp.Discord/discord-ipc-0` instead. Substitute
whichever applies for `$SOCKET` below.
To verify that only one process is listening to this socket, perform the following:
1. Quit Discord, StreamController and any other discord-like applications entirely
1. Run `lsof $SOCKET` and make sure that there are no processes returned
1. Launch Discord and StreamController again, and try to use an action
1. If it doesn't work, check `lsof $XDG_RUNTIME_DIR` again to see how many processes are accessing the socket
