"""养号配对纯逻辑测试（不联网、不碰 Telethon）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tam.warmup import MAX_PEERS, PHRASES, _pairs


def test_too_few_accounts() -> None:
    assert _pairs([], 1) == []
    assert _pairs([1], 3) == []


def test_no_self_chat() -> None:
    pairs = _pairs([1, 2, 3, 4], rounds=3)
    assert pairs and all(a != b for a, b in pairs)


def test_no_duplicate_pair() -> None:
    pairs = _pairs([1, 2, 3, 4, 5], rounds=4)
    assert len(pairs) == len(set(pairs)), "同一对不应重复发送"


def test_peer_cap() -> None:
    ids = list(range(1, 21))  # 20 个号，足够撞到上限
    pairs = _pairs(ids, rounds=15)
    per: dict[int, set[int]] = {}
    for a, b in pairs:
        per.setdefault(a, set()).add(b)
    assert all(len(v) <= MAX_PEERS for v in per.values())


def test_phrases_are_neutral() -> None:
    assert len(PHRASES) >= 10
    for p in PHRASES:
        assert "http" not in p and len(p) <= 12


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
    print("test_warmup 全部通过")
