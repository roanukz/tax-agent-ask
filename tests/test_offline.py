import asyncio
import socket

import pytest
from pytest_socket import SocketBlockedError


def test_network_sockets_are_blocked():
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM)


def test_unix_sockets_still_work():
    a, b = socket.socketpair(socket.AF_UNIX)
    a.sendall(b"ok")
    assert b.recv(2) == b"ok"
    a.close()
    b.close()


def test_asyncio_event_loop_runs():
    async def main():
        await asyncio.sleep(0)
        return "ran"

    assert asyncio.run(main()) == "ran"
