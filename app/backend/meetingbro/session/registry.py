"""In-memory ownership for reconnectable live recording sessions."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from .manager import SessionManager

logger = logging.getLogger(__name__)


@dataclass
class _Entry:
    manager: SessionManager | None
    owner: Any | None
    expiry: asyncio.Task | None = None
    cancel_requested: bool = False


class SessionRegistry:
    def __init__(self, grace_seconds: float = 45.0):
        self._entries: dict[str, _Entry] = {}
        self._grace_seconds = grace_seconds
        self._expiry_tasks: set[asyncio.Task] = set()

    def contains_meeting(self, meeting_id: str) -> bool:
        return any(entry.manager is not None and entry.manager.meeting_id == meeting_id
                   for entry in self._entries.values())

    def reserve(self, token: str, owner: Any) -> bool:
        if token in self._entries:
            return False
        self._entries[token] = _Entry(manager=None, owner=owner)
        return True

    def activate(self, token: str, owner: Any, manager: SessionManager) -> bool:
        entry = self._entries.get(token)
        if entry is None or entry.owner is not owner or entry.manager is not None:
            raise RuntimeError('Session reservation is no longer available')
        if entry.cancel_requested:
            del self._entries[token]
            return False
        entry.manager = manager
        return True

    def attach(self, token: str, owner: Any) -> tuple[str, SessionManager | None]:
        entry = self._entries.get(token)
        if entry is None:
            return 'missing', None
        if entry.owner is not None or entry.manager is None:
            return 'busy', None
        if entry.expiry:
            entry.expiry.cancel()
            entry.expiry = None
        entry.owner = owner
        return 'ok', entry.manager

    def detach(self, token: str, owner: Any) -> None:
        entry = self._entries.get(token)
        if entry is None or entry.owner is not owner:
            return
        entry.owner = None
        entry.expiry = asyncio.create_task(self._expire(token, entry))
        self._expiry_tasks.add(entry.expiry)
        entry.expiry.add_done_callback(self._expiry_tasks.discard)

    async def _expire(self, token: str, entry: _Entry) -> None:
        try:
            await asyncio.sleep(self._grace_seconds)
            if self._entries.get(token) is entry and entry.owner is None:
                # Remove before stopping so a late attach cannot take over a dying session.
                del self._entries[token]
                if entry.manager is not None:
                    await entry.manager.stop(graceful=True)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Could not stop an expired recording session")

    def remove(self, token: str, owner: Any) -> bool:
        entry = self._entries.get(token)
        if entry is None or entry.owner is not owner:
            return False
        del self._entries[token]
        if entry.expiry:
            entry.expiry.cancel()
        return True

    async def stop(self, token: str) -> bool:
        entry = self._entries.get(token)
        if entry is None:
            return False
        if entry.manager is None:
            entry.cancel_requested = True
            return True
        del self._entries[token]
        if entry.expiry:
            entry.expiry.cancel()
        if entry.manager is not None:
            await entry.manager.stop(graceful=True)
        return True

    async def close(self) -> None:
        entries = list(self._entries.values())
        self._entries.clear()
        for entry in entries:
            if entry.expiry:
                entry.expiry.cancel()
        await asyncio.gather(*list(self._expiry_tasks), return_exceptions=True)
        await asyncio.gather(*(entry.manager.stop(graceful=True) for entry in entries if entry.manager is not None), return_exceptions=True)
