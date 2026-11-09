from types import SimpleNamespace
from unittest.mock import Mock

import requests

from guiqa.native import NativeInstance


def test_health_read_timeout_uses_existing_startup_deadline(monkeypatch):
    instance = SimpleNamespace(url="http://127.0.0.1:13208", health_path="/index.php/admin/login",
        processes=[SimpleNamespace(poll=lambda: None)], directory="logs",
        http=SimpleNamespace(get=Mock(side_effect=[requests.ReadTimeout("startup"), SimpleNamespace(status_code=200)])))
    monkeypatch.setattr("guiqa.native.time.sleep", lambda seconds: None)
    NativeInstance.health(instance)
    assert instance.http.get.call_count == 2
