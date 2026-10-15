from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telethon.errors import SessionPasswordNeededError

from tam.crypto import decrypt, generate_master_key
from tam.manager import AccountManager


@pytest.mark.asyncio
@pytest.mark.parametrize("requires_password,wrong", [(True, False), (True, True), (False, False)])
async def test_only_verified_twofa_is_saved(requires_password, wrong):
    manager = AccountManager.__new__(AccountManager)
    manager.s = SimpleNamespace(master_key=generate_master_key())
    manager.db = Mock()
    manager.db.get.return_value = SimpleNamespace(phone="+12025550123")
    manager._save_session = Mock()
    client = SimpleNamespace(
        sign_in=AsyncMock(), get_me=AsyncMock(return_value=SimpleNamespace(
            id=1, username="fixture", phone="+12025550123")),
        disconnect=AsyncMock(), session=SimpleNamespace(save=lambda: "fixture"))
    if requires_password:
        client.sign_in.side_effect = [SessionPasswordNeededError(None),
                                      ValueError("invalid password") if wrong else None]
    manager._pending = {1: SimpleNamespace(client=client, phone="+12025550123", phone_code_hash="fixture")}
    if wrong:
        with pytest.raises(ValueError):
            await manager.sign_in(1, "12345", "fixture-password")
        manager.db.update.assert_not_called()
    else:
        await manager.sign_in(1, "12345", "fixture-password")
        fields = manager.db.update.call_args.kwargs
        if requires_password:
            assert fields["has_2fa"] == 1
            assert fields["twofa_enc"] != "fixture-password"
            assert decrypt(manager.s.master_key, fields["twofa_enc"]) == "fixture-password"
        else:
            assert "twofa_enc" not in fields
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_code_can_retain_same_pending_login():
    from telethon.errors import PhoneCodeInvalidError
    manager = AccountManager.__new__(AccountManager)
    manager.db = Mock()
    client = SimpleNamespace(sign_in=AsyncMock(side_effect=PhoneCodeInvalidError(None)),
                             disconnect=AsyncMock())
    pending = SimpleNamespace(client=client, phone="fixture", phone_code_hash="fixture")
    manager._pending = {1: pending}
    with pytest.raises(PhoneCodeInvalidError):
        await manager.sign_in(1, "12345", retain_invalid_code=True)
    assert manager._pending[1] is pending
    client.disconnect.assert_not_awaited()
