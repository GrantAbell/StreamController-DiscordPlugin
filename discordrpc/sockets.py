import socket
import os
import struct
import json
import re
import select

from loguru import logger as log

from .exceptions import DiscordNotOpened
from .constants import MAX_IPC_SOCKET_RANGE, SOCKET_SELECT_TIMEOUT, SOCKET_BUFFER_SIZE

SOCKET_DISCONNECTED: int = -1
SOCKET_BAD_BUFFER_SIZE: int = -2
SOCKET_SEND_TIMEOUT: int = 5
SOCKET_CONNECT_TIMEOUT: int = 2
SOCKET_RECEIVE_TIMEOUT: int = 10

class UnixPipe:
    def __init__(self):
        self.socket: socket.socket = None

    def connect(self):
        if self.socket is not None:
            log.debug("Socket already connected, disconnecting first.")
            self.disconnect()
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(SOCKET_CONNECT_TIMEOUT)
        runtime_dir = re.sub(r"\/$", "", (
            os.environ.get("XDG_RUNTIME_DIR")
            or os.environ.get("TMPDIR")
            or os.environ.get("TMP")
            or os.environ.get("TEMP")
            or "/tmp"
        ))
        # Try standard XDG path first, then Discord Flatpak app-specific path.
        # The Flatpak path handles cases where both apps are Flatpaks and the
        # portal proxy for the SC sandbox hasn't been set up yet.
        base_paths = [
            runtime_dir + "/discord-ipc-{0}",
            runtime_dir + "/app/com.discordapp.Discord/discord-ipc-{0}",
        ]
        for base_path in base_paths:
            for i in range(MAX_IPC_SOCKET_RANGE):
                path = base_path.format(i)
                try:
                    log.debug(f"Attempting to connect to socket at path: {path}")
                    self.socket.connect(path)
                    log.debug(f"Connected to socket at path: {path}")
                    self.socket.setblocking(False)
                    return
                except FileNotFoundError:
                    log.debug(f"socket {path} not found, trying next socket.")
                except Exception as ex:
                    log.debug(
                        f"failed to connect to socket {path}, trying next socket. {ex}"
                    )
        raise DiscordNotOpened

    def disconnect(self):
        if self.socket is None:
            return
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError as ex:
            # Socket might already be disconnected
            log.debug(f"Socket shutdown error (already disconnected): {ex}")
        try:
            self.socket.close()
        except OSError as ex:
            log.debug(f"Socket close error: {ex}")
        self.socket = None  # Reset so connect() creates a fresh socket

    def send(self, payload, op):
        payload_bytes = json.dumps(payload).encode("UTF-8")
        header = struct.pack("<ii", op, len(payload_bytes))
        message = header + payload_bytes
        self.socket.settimeout(SOCKET_SEND_TIMEOUT)
        self.socket.sendall(message)

    def receive(self) -> (int, str):
        data = self.socket.recv(SOCKET_BUFFER_SIZE)
        if len(data) == 0:
            return SOCKET_DISCONNECTED, {}
        header = data[:8]
        code = int.from_bytes(header[:4], "little")
        length = int.from_bytes(header[4:], "little")
        all_data = b""
        if length < 0:
            return SOCKET_BAD_BUFFER_SIZE, {}
        if length > 0:
            data = self.socket.recv(length)
            all_data += data
        return code, all_data.decode("UTF-8")
