"""Minimal WebSocket client (RFC 6455) for Slack Socket Mode, standard library only.

Supports ws:// and wss://, text messages (fragmented or not), automatic PING/PONG and
the close handshake. Binary messages are skipped; extensions and subprotocols are not
negotiated. Message contents are never logged.
"""
import asyncio
import base64
import hashlib
import os
import ssl
import struct
import time
from urllib.parse import urlsplit

_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_MESSAGE = 16 * 1024 * 1024


class ConnectionClosed(Exception):
    def __init__(self, code=None, reason=""):
        super().__init__(f"connection closed (code={code}, reason={reason!r})")
        self.code = code
        self.reason = reason


def _mask(data: bytes, key: bytes) -> bytes:
    if not data:
        return data
    stream = (key * (len(data) // 4 + 1))[:len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big")


async def connect(url, timeout=30):
    u = urlsplit(url)
    if u.scheme not in ("ws", "wss"):
        raise ValueError(f"unsupported scheme: {u.scheme!r}")
    tls = u.scheme == "wss"
    port = u.port or (443 if tls else 80)
    ctx = ssl.create_default_context() if tls else None
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(u.hostname, port, ssl=ctx, server_hostname=u.hostname if tls else None),
        timeout)
    try:
        await asyncio.wait_for(_handshake(reader, writer, u), timeout)
    except BaseException:
        writer.close()
        raise
    return WebSocket(reader, writer)


async def _handshake(reader, writer, u):
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    host = u.netloc.rsplit("@", 1)[-1]
    key = base64.b64encode(os.urandom(16)).decode()
    writer.write((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                  f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                  f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
    await writer.drain()
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as e:
        raise ConnectionError("websocket handshake failed: no valid HTTP response") from e
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or parts[1] != "101":
        raise ConnectionError(f"websocket handshake failed: {lines[0]}")
    headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
    expected = base64.b64encode(hashlib.sha1(key.encode() + _GUID).digest()).decode()
    if headers.get("sec-websocket-accept") != expected:
        raise ConnectionError("websocket handshake failed: bad Sec-WebSocket-Accept")


class WebSocket:
    def __init__(self, reader, writer):
        self._r, self._w = reader, writer
        self._lock = asyncio.Lock()
        self._close_sent = False
        self._closed = False
        self.last_rx = time.monotonic()   # any frame (pings and pongs too): the link is alive

    async def _write_frame(self, opcode, payload=b""):
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        key = os.urandom(4)
        async with self._lock:
            self._w.write(head + key + _mask(payload, key))
            await self._w.drain()

    async def _read_frame(self):
        try:
            b1, b2 = await self._r.readexactly(2)
            n = b2 & 0x7F
            if n == 126:
                (n,) = struct.unpack("!H", await self._r.readexactly(2))
            elif n == 127:
                (n,) = struct.unpack("!Q", await self._r.readexactly(8))
            if n > _MAX_MESSAGE:
                raise ConnectionClosed(1009, "message too big")
            key = await self._r.readexactly(4) if b2 & 0x80 else None
            data = await self._r.readexactly(n)
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            self._closed = True
            raise ConnectionClosed(None, "connection lost") from None
        self.last_rx = time.monotonic()
        return bool(b1 & 0x80), b1 & 0x0F, _mask(data, key) if key else data

    async def recv(self):
        """Return the next complete text message."""
        parts, kind = [], None
        while True:
            fin, op, data = await self._read_frame()
            if op == 0x9:                       # ping
                try:
                    await self._write_frame(0xA, data)
                except (ConnectionError, ssl.SSLError):
                    raise ConnectionClosed(None, "connection lost") from None
            elif op == 0xA:                     # pong
                continue
            elif op == 0x8:                     # close
                code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else None
                reason = data[2:].decode("utf-8", "replace")
                if not self._close_sent:
                    try:
                        await self._send_close(code or 1000)
                    except (ConnectionError, ssl.SSLError):
                        pass
                await self._shutdown()
                raise ConnectionClosed(code, reason)
            elif op in (0x1, 0x2):
                kind, parts = op, [data]
                if fin:
                    pass
            elif op == 0x0 and kind is not None:
                parts.append(data)
            else:
                raise ConnectionClosed(1002, "protocol error")
            if op in (0x0, 0x1, 0x2) and fin:
                msg, k = b"".join(parts), kind
                parts, kind = [], None
                if k == 0x1:
                    try:
                        return msg.decode("utf-8")
                    except UnicodeDecodeError:
                        raise ConnectionClosed(1007, "invalid utf-8") from None
            elif sum(map(len, parts)) > _MAX_MESSAGE:
                raise ConnectionClosed(1009, "message too big")

    async def send(self, text):
        if self._closed or self._close_sent:
            raise ConnectionClosed(None, "connection closed")
        await self._write_frame(0x1, text.encode("utf-8"))

    async def ping(self, data=b""):
        try:
            await self._write_frame(0x9, data)
        except (ConnectionError, ssl.SSLError):
            raise ConnectionClosed(None, "connection lost") from None

    async def _send_close(self, code):
        self._close_sent = True
        await self._write_frame(0x8, struct.pack("!H", code))

    async def _shutdown(self):
        self._closed = True
        self._w.close()
        try:
            await self._w.wait_closed()
        except Exception:
            pass

    async def close(self, code=1000):
        if self._closed:
            return
        if not self._close_sent:
            try:
                await self._send_close(code)
            except (ConnectionError, ssl.SSLError):
                pass
        await self._shutdown()
