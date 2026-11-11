import os

import pytest

os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_CHAT_ID", "0")


@pytest.fixture
def client():
    from src.backend.main import app

    app.config["TESTING"] = True # nosemgrep: python.flask.security.audit.hardcoded-config.avoid_hardcoded_config_TESTING
    with app.test_client() as client:
        yield client