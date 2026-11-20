"""wsclient against a tiny in-test asyncio WebSocket server."""
import asyncio
import base64
import hashlib
import os
import struct

import pytest

from doorbell import wsclient
from doorbell.wsclient import ConnectionClosed

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def frame(opcode, payload=b"", fin=True):
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | opcode])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack("!H", n)
    else:
        head += bytes([127]) + struct.pack("!Q", n)
    return head + payload


async def read_frame(reader):
    b1, b2 = await reader.readexactly(2)
    assert b2 & 0x80, "client frames must be masked"
    n = b2 & 0x7F
    if n == 126:
        (n,) = struct.unpack("!H", await reader.readexactly(2))
    elif n == 127:
        (n,) = struct.unpack("!Q", await reader.readexactly(8))
    key = await reader.readexactly(4)
    data = bytearray(await reader.readexactly(n))
    for i in range(n):
        data[i] ^= key[i % 4]
    return b1 & 0x0F, bytes(data)


def serve(script, bad_accept=False):
    """Run `script(reader, writer, request_line)` after the handshake; returns (url, stop)."""
    async def handler(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        lines = head.split("\r\n")
        key = next(l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1(key.encode() + GUID).digest()).decode()
        if bad_accept:
            accept = "bogus"
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        await writer.drain()
        try:
            await script(reader, writer, lines[0])
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
    return handler


def run(script, client, **kw):
    async def main():
        server = await asyncio.start_server(serve(script, **kw), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            await asyncio.wait_for(client(f"ws://127.0.0.1:{port}"), 15)
        finally:
            server.close()
    asyncio.run(main())


def test_handshake_forwards_path_and_query():
    seen = []

    async def script(r, w, request_line):
        seen.append(request_line)
        w.write(frame(1, b"hello"))
        await w.drain()
        await r.read()

    async def client(url):
        ws = await wsclient.connect(url + "/link/?ticket=abc&app_id=A1")
        assert await ws.recv() == "hello"
        await ws.close()
        await ws.close()                       # idempotent

    run(script, client)
    assert seen == ["GET /link/?ticket=abc&app_id=A1 HTTP/1.1"]


def test_text_echo():
    async def script(r, w, _):
        op, data = await read_frame(r)
        assert op == 1
        w.write(frame(1, data))
        await w.drain()
        await r.read()

    async def client(url):
        ws = await wsclient.connect(url)
        await ws.send("héllo ✓")
        assert await ws.recv() == "héllo ✓"
        await ws.close()

    run(script, client)


def test_fragmented_message_with_interleaved_ping():
    async def script(r, w, _):
        w.write(frame(1, "frag".encode(), fin=False) + frame(9, b"p")
                + frame(0, "ment".encode(), fin=False) + frame(0, b"ed"))
        await w.drain()
        op, data = await read_frame(r)
        assert (op, data) == (0xA, b"p")
        await r.read()

    async def client(url):
        ws = await wsclient.connect(url)
        assert await ws.recv() == "fragmented"
        await asyncio.sleep(0.1)
        await ws.close()

    run(script, client)


def test_server_ping_gets_pong():
    got = []

    async def script(r, w, _):
        w.write(frame(9, b"hi") + frame(0xA, b"") + frame(1, b"done"))
        await w.drain()
        got.append(await read_frame(r))
        await r.read()

    async def client(url):
        ws = await wsclient.connect(url)
        assert await ws.recv() == "done"
        await asyncio.sleep(0.1)
        await ws.close()

    run(script, client)
    assert got == [(0xA, b"hi")]


def test_large_payload_both_directions():
    big = "x" * 70_000 + "é"
    mid = "m" * 300

    async def script(r, w, _):
        for _i in range(2):
            op, data = await read_frame(r)
            w.write(frame(op, data))
        await w.drain()
        await r.read()

    async def client(url):
        ws = await wsclient.connect(url)
        await ws.send(mid)
        await ws.send(big)
        assert await ws.recv() == mid
        assert await ws.recv() == big
        await ws.close()

    run(script, client)


def test_server_close_raises_with_code_and_replies():
    got = []

    async def script(r, w, _):
        w.write(frame(8, struct.pack("!H", 4000) + b"refresh"))
        await w.drain()
        got.append(await read_frame(r))

    async def client(url):
        ws = await wsclient.connect(url)
        with pytest.raises(ConnectionClosed) as ei:
            await ws.recv()
        assert ei.value.code == 4000 and ei.value.reason == "refresh"
        with pytest.raises(ConnectionClosed):
            await ws.send("late")
        await ws.close()

    run(script, client)
    assert got[0][0] == 8 and struct.unpack("!H", got[0][1][:2])[0] == 4000


def test_eof_raises_connection_closed():
    async def script(r, w, _):
        pass                                    # handler closes immediately

    async def client(url):
        ws = await wsclient.connect(url)
        with pytest.raises(ConnectionClosed):
            await ws.recv()

    run(script, client)


def test_bad_accept_header_fails():
    async def script(r, w, _):
        await r.read()

    async def client(url):
        with pytest.raises(ConnectionError, match="Accept"):
            await wsclient.connect(url)

    run(script, client, bad_accept=True)
