import hmac
import hashlib
import secrets
import time


def _flatten(data, prefix=""):
    out = {}
    for k, v in data.items():
        key = str(k) if prefix == "" else f"{prefix}.{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key))
            continue
        if isinstance(v, bool):
            out[key] = "true" if v else "false"
            continue
        if v is None or v == "":
            continue
        out[key] = str(v)
    return out


def build_base(params: dict) -> str:
    data = {k: v for k, v in params.items() if k != "sign"}
    flat = _flatten(data)
    return "&".join(f"{k}={flat[k]}" for k in sorted(flat.keys()))


def sign(params: dict, token: str) -> str:
    base = build_base(params).encode("utf-8")
    return hmac.new(token.encode("utf-8"), base, hashlib.sha256).hexdigest().upper()


def signed_request(params: dict, shop_id, token: str) -> dict:
    p = dict(params)
    p["id"] = shop_id
    p["timestamp"] = int(time.time())
    p["nonce"] = secrets.token_hex(8)
    p["sign"] = sign(p, token)
    return p


def verify(payload: dict, token: str) -> bool:
    got = payload.get("sign")
    if not isinstance(got, str):
        return False
    return hmac.compare_digest(sign(payload, token), got.upper())
