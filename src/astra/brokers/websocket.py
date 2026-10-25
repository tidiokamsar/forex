"""Minimal RFC 6455 WebSocket client (standard library only) for JSON messages.

Scope: client handshake, masked client frames, text and binary data frames,
fragmentation, ping/pong and close. It sends text frames and accepts text or
binary frames on receive, because the cTrader docs do not state which frame
type JSON uses. Extensions and compression are not negotiated.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
from typing import Any
from urllib.parse import urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE_BYTES = 8 * 1024 * 1024

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WebSocketError(OSError):
    pass


class WebSocketClosed(WebSocketError):
    pass


def accept_key(key: str) -> str:
    digest = hashlib.sha1((key + _GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(opcode: int, payload: bytes, *, mask: bool = True, fin: bool = True) -> bytes:
    header = bytearray([(0x80 if fin else 0) | opcode])
    length = len(payload)
    mask_bit = 0x80 if mask else 0
    if length < 126:
        header.append(mask_bit | length)
    elif length < 1 << 16:
        header.append(mask_bit | 126)
        header += struct.pack("!H", length)
    else:
        header.append(mask_bit | 127)
        header += struct.pack("!Q", length)
    if not mask:
        return bytes(header) + payload
    key = os.urandom(4)
    masked = bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))
    return bytes(header) + key + masked


class WebSocketJsonTransport:
    """Blocking JSON transport. ``recv`` returns a dict, or None on timeout."""

    def __init__(self, url: str, *, timeout: float = 10.0, ssl_context: ssl.SSLContext | None = None) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("wss", "ws"):
            raise ValueError("url must use wss:// (ws:// only for local tests)")
        if parts.scheme == "ws" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("unencrypted ws:// is only allowed to a loopback test server")
        self.url = url
        self._host = parts.hostname or ""
        self._port = parts.port or (443 if parts.scheme == "wss" else 80)
        self._path = parts.path or "/"
        self._tls = parts.scheme == "wss"
        self._timeout = timeout
        self._ssl_context = ssl_context or ssl.create_default_context()
        self._sock: socket.socket | None = None
        self._buffer = b""
        self._fragments: list[bytes] = []
        self._send_lock = threading.Lock()
        self.closed = True

    def connect(self) -> None:
        raw = socket.create_connection((self._host, self._port), timeout=self._timeout)
        try:
            sock = self._ssl_context.wrap_socket(raw, server_hostname=self._host) if self._tls else raw
            self._handshake(sock)
        except BaseException:
            raw.close()
            raise

    def _handshake(self, sock: socket.socket) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self._path} HTTP/1.1\r\n"
            f"Host: {self._host}:{self._port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise WebSocketError("connection closed during handshake")
            response += chunk
            if len(response) > 16_384:
                raise WebSocketError("handshake response too large")
        head, _, rest = response.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        if not lines[0].startswith("HTTP/1.1 101"):
            raise WebSocketError(f"handshake refused: {lines[0]}")
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        if headers.get("sec-websocket-accept") != accept_key(key):
            raise WebSocketError("handshake accept key mismatch")
        self._sock = sock
        self._buffer = rest
        self.closed = False

    def send(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
        self._send_frame(OP_TEXT, payload)

    def recv(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Return the next JSON message; None if nothing arrived in ``timeout``."""

        if self._sock is None or self.closed:
            raise WebSocketClosed("not connected")
        self._sock.settimeout(timeout)
        parts = self._fragments
        while True:
            try:
                fin, opcode, payload = self._read_frame()
            except (socket.timeout, TimeoutError):
                # Nothing is consumed until a frame is complete, so a timeout
                # never loses bytes; fragments persist across calls.
                return None
            if opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                self.close()
                raise WebSocketClosed("server closed the connection")
            if opcode in (OP_TEXT, OP_BINARY, OP_CONTINUATION):
                parts.append(payload)
                if sum(len(p) for p in parts) > MAX_MESSAGE_BYTES:
                    self.close()
                    raise WebSocketError("message too large")
                if fin:
                    data = b"".join(parts)
                    parts.clear()
                    message = json.loads(data.decode("utf-8"))
                    if not isinstance(message, dict):
                        raise WebSocketError("expected a JSON object")
                    return message
                continue
            self.close()
            raise WebSocketError(f"unsupported opcode {opcode}")

    def close(self) -> None:
        if self._sock is not None and not self.closed:
            try:
                self._send_frame(OP_CLOSE, b"")
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
        self.closed = True

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._sock is None or self.closed:
            raise WebSocketClosed("not connected")
        with self._send_lock:
            self._sock.sendall(encode_frame(opcode, payload, mask=True))

    def _fill(self, count: int) -> None:
        while len(self._buffer) < count:
            chunk = self._sock.recv(65_536)
            if not chunk:
                self.closed = True
                raise WebSocketClosed("connection closed")
            self._buffer += chunk

    def _read_frame(self) -> tuple[bool, int, bytes]:
        self._fill(2)
        first, second = self._buffer[0], self._buffer[1]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        offset = 2
        if length == 126:
            self._fill(4)
            (length,) = struct.unpack("!H", self._buffer[2:4])
            offset = 4
        elif length == 127:
            self._fill(10)
            (length,) = struct.unpack("!Q", self._buffer[2:10])
            offset = 10
        if length > MAX_MESSAGE_BYTES:
            self.close()
            raise WebSocketError("frame too large")
        key_end = offset + (4 if masked else 0)
        self._fill(key_end + length)
        key = self._buffer[offset:key_end]
        payload = self._buffer[key_end : key_end + length]
        self._buffer = self._buffer[key_end + length :]
        if masked:
            payload = bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))
        return fin, opcode, payload
