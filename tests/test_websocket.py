import json
import socket
import struct
import threading
import time
import unittest

from astra.brokers.websocket import (
    OP_BINARY,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    WebSocketError,
    WebSocketJsonTransport,
    accept_key,
    encode_frame,
)


class LoopbackServer:
    """Plain ws:// test server on 127.0.0.1 that runs a scripted session."""

    def __init__(self, script, *, bad_accept=False):
        self.script = script
        self.bad_accept = bad_accept
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received = []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        conn, _ = self.listener.accept()
        self.listener.close()
        with conn:
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            key = [l.split(":", 1)[1].strip() for l in request.decode().split("\r\n") if l.lower().startswith("sec-websocket-key")][0]
            accept = "invalid" if self.bad_accept else accept_key(key)
            conn.sendall(
                f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode()
            )
            self.script(self, conn)

    def read_client_frame(self, conn):
        header = self._exact(conn, 2)
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._exact(conn, 2))
        mask = self._exact(conn, 4)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self._exact(conn, length)))
        return opcode, payload, bool(header[1] & 0x80)

    @staticmethod
    def _exact(conn, count):
        data = b""
        while len(data) < count:
            chunk = conn.recv(count - len(data))
            if not chunk:
                raise ConnectionError
            data += chunk
        return data


class WebSocketTests(unittest.TestCase):
    def connect(self, script, **kwargs):
        server = LoopbackServer(script, **kwargs)
        transport = WebSocketJsonTransport(f"ws://127.0.0.1:{server.port}/", timeout=2)
        transport.connect()
        self.addCleanup(transport.close)
        return server, transport

    def test_text_round_trip_with_masked_client_frames(self):
        def script(server, conn):
            opcode, payload, masked = server.read_client_frame(conn)
            server.received.append((opcode, json.loads(payload), masked))
            conn.sendall(encode_frame(OP_TEXT, b'{"payloadType": 2101}', mask=False))
            time.sleep(0.2)

        server, transport = self.connect(script)
        transport.send({"payloadType": 2100, "payload": {"clientId": "x"}})
        self.assertEqual(transport.recv(timeout=2), {"payloadType": 2101})
        self.assertEqual(server.received[0], (OP_TEXT, {"payloadType": 2100, "payload": {"clientId": "x"}}, True))

    def test_binary_fragmented_and_ping_frames(self):
        def script(server, conn):
            conn.sendall(encode_frame(OP_PING, b"hb", mask=False))
            conn.sendall(encode_frame(OP_BINARY, b'{"a":', mask=False, fin=False))
            conn.sendall(encode_frame(0x0, b" 1}", mask=False))
            server.received.append(server.read_client_frame(conn))
            time.sleep(0.2)

        server, transport = self.connect(script)
        self.assertEqual(transport.recv(timeout=2), {"a": 1})
        deadline = time.monotonic() + 2
        while not server.received and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(server.received[0][:2], (OP_PONG, b"hb"))

    def test_timeout_mid_frame_loses_no_bytes(self):
        frame = encode_frame(OP_TEXT, b'{"x": 42}', mask=False)

        def script(server, conn):
            conn.sendall(frame[:3])
            time.sleep(0.4)
            conn.sendall(frame[3:])
            time.sleep(0.2)

        _, transport = self.connect(script)
        self.assertIsNone(transport.recv(timeout=0.1))
        self.assertEqual(transport.recv(timeout=2), {"x": 42})

    def test_bad_accept_key_is_rejected(self):
        server = LoopbackServer(lambda s, c: time.sleep(0.2), bad_accept=True)
        transport = WebSocketJsonTransport(f"ws://127.0.0.1:{server.port}/", timeout=2)
        with self.assertRaises(WebSocketError):
            transport.connect()

    def test_plain_ws_only_to_loopback(self):
        with self.assertRaises(ValueError):
            WebSocketJsonTransport("ws://demo.ctraderapi.com:5036")
        with self.assertRaises(ValueError):
            WebSocketJsonTransport("https://demo.ctraderapi.com:5036")


if __name__ == "__main__":
    unittest.main()
