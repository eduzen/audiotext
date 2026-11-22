"""Bound transcription work before downloading audio."""

from collections import Counter, deque
from time import monotonic
from typing import Literal


class AudioAdmission:
    """Allow one active job and cap accepted uploads per user in any 60 seconds."""

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._accepted_uploads: deque[tuple[float, int]] = deque()
        self._uploads_by_user: Counter[int] = Counter()
        self._busy = False

    def reserve(self, user_id: int) -> Literal["accepted", "limited", "busy"]:
        now = monotonic()
        cutoff = now - 60
        while self._accepted_uploads and self._accepted_uploads[0][0] <= cutoff:
            _, expired_user = self._accepted_uploads.popleft()
            self._uploads_by_user[expired_user] -= 1
            if self._uploads_by_user[expired_user] == 0:
                del self._uploads_by_user[expired_user]

        if self._uploads_by_user[user_id] >= self._per_minute:
            return "limited"
        if self._busy:
            return "busy"

        self._busy = True
        self._accepted_uploads.append((now, user_id))
        self._uploads_by_user[user_id] += 1
        return "accepted"

    def release(self) -> None:
        self._busy = False
