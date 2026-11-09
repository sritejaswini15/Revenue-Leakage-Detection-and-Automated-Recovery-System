import pytest

from app.core import config
from app.core.security import TokenClaims, decode_token, encode_token, hash_password, verify_password


def test_password_hash_round_trip():
    encoded = hash_password("correct horse")
    assert verify_password("correct horse", encoded)
    assert not verify_password("wrong horse", encoded)
    assert not verify_password("anything", None)


def test_tokens_are_signed_and_tamper_evident():
    claims = TokenClaims("user-1", "org-1", "ADMIN", "session-1", 2_000_000_000)
    token = encode_token(claims, "secret")
    assert decode_token(token, "secret") == claims
    assert decode_token(token, "other-secret") is None
    assert decode_token("not-a-token", "secret") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  value  ", "value"), ('"quoted"', "quoted"), ("'single'", "single"), ('"unbalanced', '"unbalanced')],
)
def test_environment_values_are_normalized(raw, expected):
    assert config._clean(raw) == expected


def _production_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> config.Settings:
    base = {
        "ENVIRONMENT": "production",
        "AUTH_SECRET": "a" * 40,
        "RAZORPAY_WEBHOOK_SECRET": "whsec_real_secret",
        "BOOTSTRAP_ADMIN_EMAIL": "owner@merchant.test",
        "BOOTSTRAP_ADMIN_PASSWORD": "a-very-long-password",
        "DATABASE_URL": "postgresql://db/recoverx",
        "PAYMENT_PROVIDER": "simulated",
    }
    for key, value in {**base, **env}.items():
        monkeypatch.setenv(key, value)
    return config.Settings()


def test_production_accepts_complete_configuration(monkeypatch):
    _production_settings(monkeypatch).validate_production()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"AUTH_SECRET": config.DEV_AUTH_SECRET}, "AUTH_SECRET"),
        ({"RAZORPAY_WEBHOOK_SECRET": config.DEV_WEBHOOK_SECRET}, "RAZORPAY_WEBHOOK_SECRET"),
        ({"BOOTSTRAP_ADMIN_PASSWORD": "short"}, "BOOTSTRAP_ADMIN_PASSWORD"),
        ({"PAYMENT_PROVIDER": "razorpay"}, "RAZORPAY_KEY_ID"),
        ({"AUTH_REQUIRED": "false"}, "AUTH_REQUIRED"),
    ],
)
def test_production_rejects_unsafe_configuration(monkeypatch, override, message):
    with pytest.raises(RuntimeError, match=message):
        _production_settings(monkeypatch, **override).validate_production()


def test_demo_logins_are_never_created_in_production(monkeypatch):
    assert not _production_settings(monkeypatch).demo_users_enabled


def test_blank_environment_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "")
    monkeypatch.setenv("CORS_ORIGINS", "  ")
    fresh = config.Settings()
    assert fresh.AUTH_SECRET == config.DEV_AUTH_SECRET
    assert fresh.CORS_ORIGINS == ["http://localhost:3000"]
