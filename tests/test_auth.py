from fastapi.testclient import TestClient

from cloudways_monitor import auth
from cloudways_monitor.app import create_app
from cloudways_monitor.settings import Settings
from tests.helpers import valid_env

PASSWORD_HASH = (
    "pbkdf2_sha256$100000$testsalt$UYD0wkzOSvbNmj2QVVu1KD-huSLvgW5ND4OieCuF9a0"
)


def test_login_me_and_logout_use_secure_session_cookie(tmp_path) -> None:
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            DASHBOARD_USERNAME="admin",
            DASHBOARD_PASSWORD_HASH=PASSWORD_HASH,
            SESSION_COOKIE_SECURE="true",
        )
    )
    client = TestClient(create_app(settings=settings), base_url="https://testserver")

    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correct-password"},
    )
    me = client.get("/api/auth/me")
    logout = client.post("/api/auth/logout")
    logged_out_me = client.get("/api/auth/me")

    assert login.status_code == 200
    assert login.json() == {"authenticated": True, "username": "admin"}
    assert "cloudways_monitor_session=" in login.headers["set-cookie"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "Secure" in login.headers["set-cookie"]
    assert "SameSite=lax" in login.headers["set-cookie"]
    assert me.json() == {"authenticated": True, "username": "admin"}
    assert logout.status_code == 200
    assert logout.json() == {"authenticated": False, "username": None}
    assert "cloudways_monitor_session=" in logout.headers["set-cookie"]
    assert "Max-Age=0" in logout.headers["set-cookie"]
    assert logged_out_me.json() == {"authenticated": False, "username": None}


def test_api_routes_require_authentication(tmp_path) -> None:
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            DASHBOARD_USERNAME="admin",
            DASHBOARD_PASSWORD_HASH=PASSWORD_HASH,
        )
    )
    client = TestClient(create_app(settings=settings), base_url="https://testserver")

    public_health = client.get("/health")
    public_me = client.get("/api/auth/me")
    collector_health = client.get("/api/collector/health")
    alerts = client.get("/api/alerts")
    doctor = client.get("/api/doctor")
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correct-password"},
    )
    authenticated_collector_health = client.get("/api/collector/health")

    assert public_health.status_code == 200
    assert public_me.status_code == 200
    assert public_me.json() == {"authenticated": False, "username": None}
    assert collector_health.status_code == 401
    assert alerts.status_code == 401
    assert doctor.status_code == 401
    assert login.status_code == 200
    assert authenticated_collector_health.status_code == 200


def test_signed_session_tokens_expire(monkeypatch) -> None:
    monkeypatch.setattr(auth.time, "time", lambda: 1000)
    token = auth.create_session_token(
        username="admin",
        secret="a-session-secret-with-enough-entropy",
    )

    monkeypatch.setattr(
        auth.time,
        "time",
        lambda: 1000 + auth.SESSION_MAX_AGE_SECONDS + 1,
    )

    assert (
        auth.verify_session_token(
            token=token,
            secret="a-session-secret-with-enough-entropy",
        )
        is None
    )

def test_password_hash_accepts_docker_safe_colon_format() -> None:
    password_hash = (
        "pbkdf2_sha256:100000:testsalt:UYD0wkzOSvbNmj2QVVu1KD-huSLvgW5ND4OieCuF9a0"
    )

    assert auth.verify_password(
        password="correct-password",
        password_hash=password_hash,
    )
    assert not auth.verify_password(
        password="wrong-password",
        password_hash=password_hash,
    )

def test_hash_password_generates_docker_safe_verifiable_hash() -> None:
    password_hash = auth.hash_password("correct-password")

    assert "$" not in password_hash
    assert auth.verify_password(
        password="correct-password",
        password_hash=password_hash,
    )

def test_login_rejects_invalid_credentials(tmp_path) -> None:
    settings = Settings.from_env(
        valid_env(
            SQLITE_PATH=str(tmp_path / "cloudways-monitor.sqlite3"),
            DASHBOARD_USERNAME="admin",
            DASHBOARD_PASSWORD_HASH=PASSWORD_HASH,
        )
    )
    client = TestClient(create_app(settings=settings), base_url="https://testserver")

    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password"}
    assert "set-cookie" not in response.headers
