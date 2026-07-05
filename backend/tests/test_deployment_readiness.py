import os
import sys
import pytest
from fastapi.testclient import TestClient

# Ensure backend dir is on path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from server import app

@pytest.fixture
def client():
    return TestClient(app)

def test_health_endpoint(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["graphMode"] in ("neo4j", "memory")
    assert isinstance(data["mongoReachable"], bool)

    # Assert no secrets are leaked
    for key, value in data.items():
        val_str = str(value).lower()
        assert "password" not in val_str
        assert "mongodb://" not in val_str
        assert "bolt://" not in val_str

def test_cors_logic():
    def get_cors_settings(origins_env):
        cors_origins_env = origins_env.strip()
        if not cors_origins_env:
            allow_origins = ["*"]
        else:
            allow_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]

        if "*" in allow_origins:
            allow_credentials = False
        else:
            allow_credentials = True
        return allow_origins, allow_credentials

    # CORS wildcard disables credentials
    origins, creds = get_cors_settings("")
    assert "*" in origins
    assert creds is False

    origins, creds = get_cors_settings(" * ")
    assert "*" in origins
    assert creds is False

    # Explicit origins enable credentials
    origins, creds = get_cors_settings("https://gv.example.com, https://sentinel.example.com")
    assert "https://gv.example.com" in origins
    assert "https://sentinel.example.com" in origins
    assert creds is True

    # Whitespace in CORS_ORIGINS is normalized
    origins, creds = get_cors_settings("  https://a.com  ,   https://b.com   ")
    assert origins == ["https://a.com", "https://b.com"]
    assert creds is True

def test_frontend_env_example():
    fe_env_path = os.path.join(os.path.dirname(backend_dir), "frontend", ".env.example")
    assert os.path.exists(fe_env_path)
    with open(fe_env_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "EXPO_PUBLIC_BACKEND_URL=" in content
    # Contains only placeholder
    assert "https://your-backend.example.com" in content
    # Verify no localhost or real endpoint
    assert "localhost" not in content
    assert "127.0.0.1" not in content

def test_backend_env_example():
    be_env_path = os.path.join(backend_dir, ".env.example")
    assert os.path.exists(be_env_path)
    with open(be_env_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "NEO4J_PASSWORD=" in content
    assert "your_password_here" in content
    assert "your_api_key_here" in content
    # No real secrets
    assert "AuraDB" not in content

def test_dockerfile_production_command():
    df_path = os.path.join(backend_dir, "Dockerfile")
    assert os.path.exists(df_path)
    with open(df_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "uvicorn server:app" in content
    assert "--host 0.0.0.0" in content
    assert "--port" in content
    assert "--reload" not in content

def test_smoke_url_validation():
    from scripts.deployment_smoke_test import validate_url

    # Valid URLs
    ok, res = validate_url("http://localhost:8000")
    assert ok is True
    ok, res = validate_url("https://sentinel-api.example.com")
    assert ok is True

    # Malformed / Wrong scheme URLs
    ok, res = validate_url("ftp://localhost:8000")
    assert ok is False
    assert "scheme" in res

    ok, res = validate_url("localhost:8000")
    assert ok is False
    assert "scheme" in res or "host" in res

    # Credentials URLs
    ok, res = validate_url("http://user:pass@localhost:8000")
    assert ok is False
    assert "Credentials" in res


from unittest.mock import patch, MagicMock

@patch("sys.argv", ["deployment_smoke_test.py", "http://localhost:8000"])
@patch("urllib.request.urlopen")
def test_smoke_test_neo4j_success(mock_urlopen):
    from scripts.deployment_smoke_test import run_smoke_test

    def side_effect(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.__enter__.return_value = mock_resp
        if "/api/health" in url:
            mock_resp.read.return_value = b'{"status": "ok", "graphMode": "neo4j", "mongoReachable": false}'
        else:
            mock_resp.read.return_value = b'{}'
        return mock_resp

    mock_urlopen.side_effect = side_effect

    with pytest.raises(SystemExit) as exc_info:
        run_smoke_test()
    assert exc_info.value.code == 0


@patch("sys.argv", ["deployment_smoke_test.py", "http://localhost:8000"])
@patch("urllib.request.urlopen")
def test_smoke_test_memory_failure(mock_urlopen):
    from scripts.deployment_smoke_test import run_smoke_test

    def side_effect(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.__enter__.return_value = mock_resp
        if "/api/health" in url:
            mock_resp.read.return_value = b'{"status": "ok", "graphMode": "memory", "mongoReachable": true}'
        else:
            mock_resp.read.return_value = b'{}'
        return mock_resp

    mock_urlopen.side_effect = side_effect

    with pytest.raises(SystemExit) as exc_info:
        run_smoke_test()
    assert exc_info.value.code == 1

