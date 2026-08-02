"""限速与退避。

Telegram 对 MTProto 客户端有隐式速率限制，超限返回 FloodWaitError(seconds)。
策略：
1. 每账号一个令牌桶，限制稳态 QPS；
2. 批量动作之间加入随机人性化间隔；
3. 捕获 FloodWait 后按服务端给定秒数休眠，超过阈值直接放弃并标记账号。
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict


class TokenBucket:
    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = rate
        self.capacity = capacity if capacity is not None else max(1.0, rate)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, amount: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity, self.tokens + (now - self.updated) * self.rate
                )
                self.updated = now
                if self.tokens >= amount:
                    self.tokens -= amount
                    return
                await asyncio.sleep((amount - self.tokens) / self.rate)


class RateLimiter:
    """按账号维度隔离的限速器。"""

    def __init__(self, rate: float) -> None:
        self.rate = rate
        self._buckets: dict[int, TokenBucket] = defaultdict(lambda: TokenBucket(rate))

    async def wait(self, account_id: int) -> None:
        await self._buckets[account_id].acquire()


async def human_delay(min_s: float, max_s: float) -> None:
    await asyncio.sleep(random.uniform(min_s, max_s))
