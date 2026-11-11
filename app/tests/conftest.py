import os
from unittest.mock import patch

import pytest

os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("TELEGRAM_CHAT_ID", "0")


@pytest.fixture(scope="session")
def flask_app():
    # init_db() runs at import time, so keep it from opening a real connection
    with patch("psycopg.connect"):
        from src.backend.main import app

    app.config["TESTING"] = True # nosemgrep: python.flask.security.audit.hardcoded-config.avoid_hardcoded_config_TESTING
    return app


@pytest.fixture
def client(flask_app):
    with flask_app.test_client() as client:
        yield client