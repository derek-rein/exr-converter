from __future__ import annotations

import uuid

import pytest
from PySide6.QtTest import QTest

from src.core import handoff
from src.services.single_instance import InstanceServer


def send_while_pumping(paths: list[str]) -> bool:
    """Send from another thread, as another process would, while this one runs events.

    Windows pipes hand the data over only when the server reads it, so a client
    in the server's own thread would wait for itself.
    """
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(handoff.send_paths, paths)
        while not future.done():
            QTest.qWait(10)
        return future.result()


@pytest.fixture(autouse=True)
def private_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXR_CONVERTER_HANDOFF", f"exr-test-{uuid.uuid4().hex[:10]}")


def test_message_round_trip() -> None:
    paths = ["/a/b.exr", r"C:\shots\plate.0001.dpx", "/with space/é.exr"]
    assert handoff.decode(handoff.encode(paths)) == paths


@pytest.mark.parametrize(
    "data",
    [b"", b"not json", b"[]", b'{"v": 2, "paths": ["/a"]}', b'{"v": 1}', b'{"v": 1, "paths": 5}'],
)
def test_garbage_is_ignored(data: bytes) -> None:
    assert handoff.decode(data) == []


def test_non_string_paths_are_dropped() -> None:
    assert handoff.decode(b'{"v": 1, "paths": ["/a", 3, null, "", "/b"]}') == ["/a", "/b"]


def test_no_running_instance(qapp) -> None:
    assert handoff.send_paths(["/a/b.exr"]) is False


def test_a_second_launch_reaches_the_first(qapp) -> None:
    server = InstanceServer()
    received: list[list[str]] = []
    server.paths_received.connect(received.append)
    try:
        assert server.start()
        assert send_while_pumping(["/shots/plate.1001.exr"]) is True
        for _ in range(40):
            if received:
                break
            QTest.qWait(25)
        assert received == [["/shots/plate.1001.exr"]]
    finally:
        server.close()


def test_a_second_server_does_not_take_over(qapp) -> None:
    first, second = InstanceServer(), InstanceServer()
    try:
        assert first.start()
        assert second.start() is False
    finally:
        first.close()
        second.close()
