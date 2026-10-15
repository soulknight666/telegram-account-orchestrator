from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telethon.errors import (AuthKeyUnregisteredError, SessionRevokedError,
                             SessionExpiredError, AuthKeyDuplicatedError)

from tam.manager import AccountManager, SessionUnauthorizedError


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [AuthKeyUnregisteredError(None), SessionRevokedError(None),
                                   SessionExpiredError(None), AuthKeyDuplicatedError(None),
                                   SessionUnauthorizedError("invalid session")])
async def test_invalid_session_is_not_generic_error(error):
    manager = AccountManager.__new__(AccountManager)
    manager.db = Mock()
    manager.db.get.return_value = SimpleNamespace(session_enc="fixture")
    @asynccontextmanager
    async def session(aid):
        raise error
        yield
    manager.session = session
    result = await manager.health_check(1)
    assert result["status"] == "unauthorized"
    assert manager.db.update.call_args.kwargs["status"] == "unauthorized"


@pytest.mark.asyncio
async def test_verify_saved_session_loads_existing_credentials():
    manager = AccountManager.__new__(AccountManager)
    manager.db = Mock()
    account = SimpleNamespace(session_enc="encrypted-fixture")
    manager.db.get.return_value = account
    manager._load_session = Mock(return_value="saved-session-fixture")
    client = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock(),
                             is_user_authorized=AsyncMock(return_value=True))
    manager._build_client = Mock(return_value=client)
    assert await manager.login_session_authorized(1)
    manager._build_client.assert_called_once_with(account, "saved-session-fixture")
    client.disconnect.assert_awaited_once()
