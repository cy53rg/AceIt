"""UIBridge enqueues outbound WS messages for the async sender task."""
from __future__ import annotations

import asyncio
import json
import threading

from atlas_daemon import UIBridge


def test_ui_bridge_broadcast_enqueues_message():
    bridge = UIBridge()
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=_run_loop, daemon=True, name="test-ws-loop")
    thread.start()
    bridge._loop = loop
    bridge.attach(object(), queue)

    try:
        bridge.broadcast({"type": "chunk", "text": "hi"})
        fut = asyncio.run_coroutine_threadsafe(queue.get(), loop)
        raw = fut.result(timeout=2)
        assert json.loads(raw)["text"] == "hi"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3.0)
        loop.close()
