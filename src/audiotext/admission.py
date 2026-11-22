"""Bound transcription work before downloading audio."""

from collections import Counter, deque
from time import monotonic
from typing import Literal


class AudioAdmission:
    """Allow one active job and cap accepted uploads per user in any 60 seconds."""

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._accepted: deque[tuple[float, int]] = deque()
        self._counts: Counter[int] = Counter()
        self._busy = False

    def reserve(self, user_id: int) -> Literal["accepted", "limited", "busy"]:
        now = monotonic()
        while self._accepted and self._accepted[0][0] <= now - 60:
            _, expired_user = self._accepted.popleft()
            self._counts[expired_user] -= 1
            if not self._counts[expired_user]:
                del self._counts[expired_user]

        if self._counts[user_id] >= self._per_minute:
            return "limited"
        if self._busy:
            return "busy"

        self._busy = True
        self._accepted.append((now, user_id))
        self._counts[user_id] += 1
        return "accepted"

    def release(self) -> None:
        self._busy = False
