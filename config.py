import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
POLICY_DIR = BASE_DIR / "policies"
DEFAULT_DB_PATH = DATA_DIR / "ridewise_dev.db"


def get_setting(name: str, default: str | None = None) -> str | None:
    """Read a non-empty environment variable, falling back to the default."""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def get_database_path() -> str:
    configured = get_setting("RIDEWISE_DB_PATH")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = BASE_DIR / path
        return str(path)
    return str(DEFAULT_DB_PATH)


def get_policy_dir() -> str:
    configured = get_setting("RIDEWISE_POLICY_DIR")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = BASE_DIR / path
        return str(path)
    return str(POLICY_DIR)


def _get_neostats_url() -> str | None:
    # Support both project variable names and base or full completion URLs.
    return get_setting("NEOSTATS_API_URL") or get_setting("NEOSTATS_BASE_URL")


def is_llm_configured() -> bool:
    return bool(
        _get_neostats_url()
        and get_setting("NEOSTATS_API_KEY")
        and get_setting("NEOSTATS_MODEL")
    )


def get_llm_config() -> dict[str, Any]:
    """Return validated NeoStats LLM settings without logging credentials."""
    raw_timeout = get_setting("NEOSTATS_TIMEOUT", "30") or "30"
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("NEOSTATS_TIMEOUT must be an integer number of seconds.") from exc
    if timeout < 1:
        raise ValueError("NEOSTATS_TIMEOUT must be at least 1 second.")

    url = _get_neostats_url()
    if url:
        url = url.rstrip("/")
        if url.endswith("/chat/completions"):
            url = url[: -len("/chat/completions")]

    return {
        "base_url": url,
        "api_key": get_setting("NEOSTATS_API_KEY"),
        "model": get_setting("NEOSTATS_MODEL"),
        "timeout": timeout,
    }
