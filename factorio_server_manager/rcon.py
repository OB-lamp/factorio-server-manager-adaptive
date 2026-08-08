from __future__ import annotations

import socket
import struct


class RconError(RuntimeError):
    pass


SERVERDATA_RESPONSE_VALUE = 0
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_AUTH = 3


def execute_rcon(
    host: str,
    port: int,
    password: str,
    command: str,
    *,
    timeout: float = 5.0,
) -> str:
    with socket.create_connection((host, port), timeout=timeout) as connection:
        connection.settimeout(timeout)
        connection.sendall(_packet(1, SERVERDATA_AUTH, password))

        authenticated = False
        for _ in range(3):
            request_id, packet_type, _body = _read_packet(connection)
            if packet_type == SERVERDATA_AUTH_RESPONSE:
                if request_id == -1:
                    raise RconError("RCON 身份验证失败")
                authenticated = True
                break
        if not authenticated:
            raise RconError("RCON 没有返回身份验证结果")

        connection.sendall(_packet(2, SERVERDATA_EXECCOMMAND, command))
        request_id, packet_type, body = _read_packet(connection)
        if request_id != 2 or packet_type != SERVERDATA_RESPONSE_VALUE:
            raise RconError("RCON 返回了意外的数据包")
        return body


def _packet(request_id: int, packet_type: int, body: str) -> bytes:
    encoded = body.encode("utf-8")
    payload = struct.pack("<ii", request_id, packet_type) + encoded + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def _read_packet(connection: socket.socket) -> tuple[int, int, str]:
    length = struct.unpack("<i", _read_exact(connection, 4))[0]
    if length < 10 or length > 4 * 1024 * 1024:
        raise RconError(f"RCON 数据包长度无效：{length}")
    payload = _read_exact(connection, length)
    request_id, packet_type = struct.unpack("<ii", payload[:8])
    body = payload[8:-2].decode("utf-8", errors="replace")
    return request_id, packet_type, body


def _read_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise RconError("RCON 连接意外关闭")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
