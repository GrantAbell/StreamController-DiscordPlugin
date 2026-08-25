#!/usr/bin/env python3
"""Republish Discord's sandbox-private RPC socket, on the host.

This script is the one piece of the plugin that runs *outside* every sandbox:
the backend starts it through `flatpak-spawn --host` when Discord is running
but its IPC socket has gone missing from $XDG_RUNTIME_DIR/app/<discord id>.

Discord's real socket only ever exists inside Discord's own Flatpak sandbox.
Discord's launcher normally publishes a copy of it on the host with a socat
relay, but ties that relay to a single `flatpak run` invocation instead of to
Discord itself. Launch Discord again while it is already running -- clicking
the launcher to raise the window is enough -- and the second invocation can
take the relay down on its way out. Discord keeps running with no socket for
anything to connect to, and nothing recreates it short of a full restart of
Discord.

So this is the same relay, supervised by the right thing: it lives exactly as
long as the Discord process it serves, and it reaches into that process's mount
namespace through /proc/<pid>/root, which works because Discord runs as us.

Exit status: 0 when a relay is listening (freshly bound, or already there),
EXIT_NO_DISCORD when Discord simply is not running, 1 for a real failure.
"""

import argparse
import os
import socket
import stat
import sys
import threading
import time

# The Flatpak build of Discord always execs this path inside its sandbox.
DISCORD_EXECUTABLE = b"/app/discord/Discord"
# Sockets Discord may have opened inside its sandbox, in the order RPC clients
# try them.
MAX_IPC_SOCKET_RANGE = 10
# Told apart from a genuine failure so the caller can back off politely while
# Discord is closed, which is most of the time.
EXIT_NO_DISCORD = 2
BUFFER_SIZE = 65536
PID_POLL_INTERVAL = 2.0
PROBE_TIMEOUT = 1.0


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Someone else's process now owns that pid; ours is gone.
        return False
    return True


def is_socket(path: str) -> bool:
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def has_listener(path: str) -> bool:
    """True when something is still accepting connections on this socket."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(PROBE_TIMEOUT)
    try:
        probe.connect(path)
    except OSError:
        return False
    finally:
        probe.close()
    return True


def find_discord(runtime_dir: str):
    """Locate a live Discord and the socket it is listening on inside its sandbox.

    Returns (pid, target path) or (None, None). The target is expressed through
    /proc/<pid>/root, which is how the host reaches a path that only exists in
    that process's mount namespace.
    """
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                argv = handle.read().split(b"\0")
        except OSError:
            continue  # process exited, or is not ours to read
        if not argv or argv[0] != DISCORD_EXECUTABLE:
            continue
        # Skip the renderer/gpu/utility children: they share the namespace but
        # come and go, and the relay should outlive them.
        if len(argv) > 1 and argv[1].startswith(b"--type="):
            continue
        for index in range(MAX_IPC_SOCKET_RANGE):
            target = f"/proc/{pid}/root{runtime_dir}/discord-ipc-{index}"
            if is_socket(target):
                return pid, target
    return None, None


def pump(source: socket.socket, sink: socket.socket):
    """Forward one direction until it closes, then half-close the far end."""
    try:
        while True:
            data = source.recv(BUFFER_SIZE)
            if not data:
                break
            sink.sendall(data)
    except OSError:
        pass
    finally:
        try:
            sink.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def serve(client: socket.socket, target: str):
    upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        upstream.connect(target)
    except OSError:
        client.close()
        upstream.close()
        return
    directions = [
        threading.Thread(target=pump, args=(client, upstream), daemon=True),
        threading.Thread(target=pump, args=(upstream, client), daemon=True),
    ]
    for direction in directions:
        direction.start()
    for direction in directions:
        direction.join()
    client.close()
    upstream.close()


def watch(pid: int, listen_path: str, listen_inode: int):
    """Tear the relay down once the Discord it serves is gone."""
    while pid_alive(pid):
        time.sleep(PID_POLL_INTERVAL)
    try:
        # Only remove the socket if it is still the one we bound: Discord
        # restarting inside the poll interval may already have published a new
        # one at this path.
        if os.stat(listen_path).st_ino == listen_inode:
            os.unlink(listen_path)
    except OSError:
        pass
    os._exit(0)


def daemonize():
    """Detach so `flatpak-spawn --host` returns as soon as the relay is bound."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    for descriptor in (0, 1, 2):
        os.dup2(devnull, descriptor)
    os.close(devnull)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--listen", required=True, help="host path to publish the relay socket at"
    )
    parser.add_argument(
        "--runtime-dir",
        default=os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
        help="XDG runtime directory, as seen inside Discord's sandbox",
    )
    args = parser.parse_args(argv)

    pid, target = find_discord(args.runtime_dir)
    if pid is None:
        print("no running Discord with a reachable IPC socket", file=sys.stderr)
        return EXIT_NO_DISCORD

    if os.path.exists(args.listen):
        if has_listener(args.listen):
            print(f"{args.listen} already has a listener", file=sys.stderr)
            return 0
        os.unlink(args.listen)  # stale socket from a relay that was killed

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(args.listen)
        listener.listen(16)
    except OSError as ex:
        print(f"failed to bind {args.listen}: {ex}", file=sys.stderr)
        return 1
    listen_inode = os.stat(args.listen).st_ino

    print(f"relaying {args.listen} -> {target} (discord pid {pid})")
    sys.stdout.flush()  # os._exit() in daemonize() will not flush for us
    daemonize()

    threading.Thread(
        target=watch, args=(pid, args.listen, listen_inode), daemon=True
    ).start()
    while True:
        try:
            client, _ = listener.accept()
        except OSError:
            break
        threading.Thread(target=serve, args=(client, target), daemon=True).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
