"""WebSocket connection hub.

A process-local registry of sockets keyed by user id, fronted by a Redis
pub/sub fan-out. The Redis layer is what makes this correct with more than one
uvicorn worker: worker A delivering a message to a recipient connected to
worker B publishes to Redis, and B pushes it down its own socket.

If Redis is unavailable the hub still works for sockets on the same process,
and logs the degradation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from typing import Any

import redis.asyncio as aioredis
from fastapi import WebSocket

from app.core.config import settings

logger = logging.getLogger(__name__)

CHANNEL = "amora:ws"


class ConnectionManager:
    def __init__(self) -> None:
        # One user may have several live sockets (phone + web tab).
        self._sockets: dict[uuid.UUID, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._redis: aioredis.Redis | None = None
        self._pubsub_task: asyncio.Task | None = None

    # --- lifecycle --------------------------------------------------------
    async def start(self) -> None:
        """Subscribe to the cross-worker fan-out channel.

        Skipped entirely when `ws_fanout_enabled` is false, which is correct
        for a single-instance deployment: there is no other worker to reach,
        so publishing to Redis would be a wasted round trip on every message.
        It also avoids depending on pub/sub, which not every managed Redis
        supports over the TCP protocol.
        """
        if not settings.ws_fanout_enabled:
            logger.info(
                "WebSocket fan-out disabled; delivering within this process "
                "only. Correct for a single-instance deployment."
            )
            self._redis = None
            return

        try:
            self._redis = aioredis.from_url(
                settings.redis_url, encoding="utf-8", decode_responses=True
            )
            await self._redis.ping()
            self._pubsub_task = asyncio.create_task(self._listen())
            logger.info("WebSocket fan-out connected to Redis")
        except Exception as exc:
            logger.warning(
                "Redis fan-out unavailable (%s); WebSocket delivery is "
                "single-process only",
                exc,
            )
            self._redis = None

    async def stop(self) -> None:
        if self._pubsub_task:
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except asyncio.CancelledError:
                # Expected: this is how the listener task is stopped.
                pass
            except Exception as exc:
                logger.warning("Fan-out listener failed during shutdown: %s", exc)
            self._pubsub_task = None
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def _listen(self) -> None:
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL)
        try:
            async for raw in pubsub.listen():
                if raw.get("type") != "message":
                    continue
                try:
                    envelope = json.loads(raw["data"])
                    user_id = uuid.UUID(envelope["user_id"])
                except (ValueError, KeyError, TypeError):
                    continue
                await self._deliver_local(user_id, envelope["payload"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("WebSocket fan-out listener stopped: %s", exc)
        finally:
            await pubsub.unsubscribe(CHANNEL)
            await pubsub.aclose()

    # --- registry ---------------------------------------------------------
    async def connect(self, user_id: uuid.UUID, socket: WebSocket) -> None:
        await socket.accept()
        async with self._lock:
            self._sockets[user_id].add(socket)

    async def disconnect(self, user_id: uuid.UUID, socket: WebSocket) -> None:
        async with self._lock:
            self._sockets[user_id].discard(socket)
            if not self._sockets[user_id]:
                self._sockets.pop(user_id, None)

    def is_online(self, user_id: uuid.UUID) -> bool:
        return bool(self._sockets.get(user_id))

    # --- delivery ---------------------------------------------------------
    async def send_to_user(self, user_id: uuid.UUID, payload: dict[str, Any]) -> None:
        """Deliver to every socket this user has, on any worker."""
        if self._redis is not None:
            try:
                await self._redis.publish(
                    CHANNEL,
                    json.dumps({"user_id": str(user_id), "payload": payload}),
                )
                return
            except Exception as exc:
                logger.warning("Fan-out publish failed, delivering locally: %s", exc)
        await self._deliver_local(user_id, payload)

    async def _deliver_local(self, user_id: uuid.UUID, payload: dict[str, Any]) -> None:
        sockets = list(self._sockets.get(user_id, ()))
        if not sockets:
            return
        dead: list[WebSocket] = []
        for socket in sockets:
            try:
                await socket.send_json(payload)
            except Exception:
                dead.append(socket)
        if dead:
            async with self._lock:
                for socket in dead:
                    self._sockets.get(user_id, set()).discard(socket)


manager = ConnectionManager()
