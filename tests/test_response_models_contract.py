"""Contract checks for additive response models and OpenAPI exposure."""

import os

# Importing tam.api builds the application at module import time and therefore
# loads Settings immediately.  Keep this contract test hermetic: CI runners
# do not have the developer's local .env, and this value is synthetic only.
os.environ.setdefault("TAM_MASTER_KEY", "test-response-models-" + "0" * 48)

from tam.api import app
from tam.api import (
    AccountOut,
    BatchPreviewOut,
    MixedImportOut,
    SettingsOut,
    TdataInspectOut,
    TaskOut,
    ToolboxBatchOut,
)


def _schema(path: str, method: str = "get"):
    operation = app.openapi()["paths"][path][method]
    return operation["responses"]["200"]["content"]["application/json"]["schema"]


def test_frontend_routes_publish_response_models():
    expected = {
        "/api/accounts": ("get", "AccountOut"),
        "/api/tasks": ("get", "TaskOut"),
        "/api/tasks/{task_id}": ("get", "TaskOut"),
        "/api/batch/preview": ("post", "BatchPreviewOut"),
        "/api/toolbox/{op}/batch": ("post", "ToolboxBatchOut"),
        "/api/import/mixed": ("post", "MixedImportOut"),
        "/api/import/tdata/inspect": ("post", "TdataInspectOut"),
        "/api/settings": ("get", "SettingsOut"),
    }
    for path, (method, model) in expected.items():
        schema = _schema(path, method)
        assert model in repr(schema), (path, method, schema)


def test_models_preserve_additive_fields():
    samples = [
        (AccountOut, {"id": 1, "label": "A", "future": 1}),
        (TaskOut, {"id": 1, "kind": "check", "status": "done", "future": 1}),
        (ToolboxBatchOut, {"results": [], "future": 1}),
        (BatchPreviewOut, {"run": [], "skip": [], "future": 1}),
        (MixedImportOut, {"dry_run": True, "total": 0, "items": [], "future": 1}),
        (TdataInspectOut, {"filename": "x.zip", "total": 0, "items": [], "future": 1}),
        (SettingsOut, {"settings": {}, "future": 1}),
    ]
    for model, payload in samples:
        assert model.model_validate(payload).model_dump()["future"] == 1
