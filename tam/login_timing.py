"""Per-provider timing estimates. Stores only bounded numbers, never credential URLs."""
from __future__ import annotations

import hashlib
import json
import math
from urllib.parse import urlsplit


def refresh_floor(url: str) -> float:
    return 60.0 if urlsplit(url).path.rstrip('/').lower().endswith('/gethtml') else 1.0


class LoginTiming:
    """Probe 10% earlier after three first-query successes; back off after a miss.

    First-query latency is an upper bound, not the actual delivery time. Learning
    is only committed after Telegram accepts a code, and network failures do not
    count as delivery samples. The user's interval remains a hard lower bound.
    """
    def __init__(self, db, url: str, delay: float, interval: float, enabled: bool,
                 provider_interval: float = 0):
        self.db, self.enabled = db, enabled
        self.seed = float(delay)
        parsed = urlsplit(url)
        family = 'gethtml' if refresh_floor(url) == 60 else 'generic'
        identity = f'{parsed.scheme}://{parsed.hostname}:{parsed.port or 0}/{family}'
        self.key = 'login_timing.v1.' + hashlib.sha256(identity.encode()).hexdigest()
        self.interval = max(float(interval), refresh_floor(url), provider_interval)
        self.delay = self.seed
        if enabled:
            self.delay = self._load().get('delay', self.seed)

    def _load(self) -> dict:
        try:
            data = json.loads(self.db.get_setting(self.key) or '{}')
            delay = float(data['delay'])
            streak = int(data.get('streak', 0))
            if math.isfinite(delay) and 0 <= delay <= 600 and 0 <= streak < 3:
                return {'delay':delay, 'streak':streak}
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
        return {}

    def success(self, elapsed: float, misses: int) -> None:
        if not self.enabled or not math.isfinite(elapsed):
            return
        state = self._load()
        current = state.get('delay', self.delay)
        streak = state.get('streak', 0)
        if misses:
            current = min(600.0, max(current, min(elapsed, self.delay + self.interval)))
            streak = 0
        elif abs(current - self.delay) < 0.01:
            streak += 1
            if streak >= 3:
                current = max(1.0, current - min(5.0, current * 0.1))
                streak = 0
        try:
            self.db.set_setting(self.key, json.dumps({'delay':round(current, 2), 'streak':streak}))
        except (AttributeError, OSError):
            pass
