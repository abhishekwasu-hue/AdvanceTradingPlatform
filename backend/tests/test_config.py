import pytest

from app.core import config


def test_validate_production_config_is_a_noop_outside_production(monkeypatch):
    monkeypatch.setattr(config, "ENVIRONMENT", "development")
    monkeypatch.setattr(config, "JWT_SECRET_KEY", config._INSECURE_DEFAULT_JWT_SECRET)
    monkeypatch.setattr(config, "SECRETS_ENCRYPTION_KEY", None)
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["*"])
    config.validate_production_config()  # must not raise, even with every insecure default set


def test_validate_production_config_rejects_default_jwt_secret(monkeypatch):
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "JWT_SECRET_KEY", config._INSECURE_DEFAULT_JWT_SECRET)
    monkeypatch.setattr(config, "SECRETS_ENCRYPTION_KEY", "a-real-fernet-key")
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["https://app.example.com"])
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        config.validate_production_config()


def test_validate_production_config_rejects_missing_encryption_key(monkeypatch):
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "a-real-secret")
    monkeypatch.setattr(config, "SECRETS_ENCRYPTION_KEY", None)
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["https://app.example.com"])
    with pytest.raises(RuntimeError, match="SECRETS_ENCRYPTION_KEY"):
        config.validate_production_config()


def test_validate_production_config_rejects_wildcard_cors(monkeypatch):
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "a-real-secret")
    monkeypatch.setattr(config, "SECRETS_ENCRYPTION_KEY", "a-real-fernet-key")
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["*"])
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        config.validate_production_config()


def test_validate_production_config_passes_with_secure_settings(monkeypatch):
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "a-real-secret")
    monkeypatch.setattr(config, "SECRETS_ENCRYPTION_KEY", "a-real-fernet-key")
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["https://app.example.com"])
    config.validate_production_config()  # must not raise
