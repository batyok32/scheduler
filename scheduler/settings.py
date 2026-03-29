"""
Django settings for the Retell ↔ Cal.com scheduler backend.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me-in-production")
DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() in ("1", "true", "yes")


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes")


# Cal.com: full request/response logging (stderr + structured). Unset → use legacy RETELL_LOG_FULL_RESPONSES, else DEBUG.
CALCOM_VERBOSE_LOGS = _env_bool(
    "CALCOM_VERBOSE_LOGS",
    _env_bool("RETELL_LOG_FULL_RESPONSES", DEBUG),
)
# Retell: inbound/outbound JSON + stderr debug lines for the webhook (default off).
RETELL_LOG_VERBOSE = _env_bool("RETELL_LOG_VERBOSE", False)
# Legacy alias; only used above as fallback when CALCOM_VERBOSE_LOGS is unset — keep for .env compatibility.
RETELL_LOG_FULL_RESPONSES = _env_bool("RETELL_LOG_FULL_RESPONSES", DEBUG)

def _hostname_from_trusted_origin(origin: str) -> str | None:
    """Extract host from ``https://subdomain.example.com`` for ``ALLOWED_HOSTS``."""
    try:
        parsed = urlparse(origin.strip())
        if not parsed.netloc:
            return None
        return parsed.netloc.split("@")[-1].split(":")[0]
    except Exception:
        return None


_CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]
CSRF_TRUSTED_ORIGINS = _CSRF_TRUSTED_ORIGINS

_allowed_hosts = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if h.strip()
]
for _origin in CSRF_TRUSTED_ORIGINS:
    _h = _hostname_from_trusted_origin(_origin)
    if _h and _h not in _allowed_hosts:
        _allowed_hosts.append(_h)
ALLOWED_HOSTS = _allowed_hosts

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "booking",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "booking.middleware.CorrelationIdMiddleware",
]

ROOT_URLCONF = "scheduler.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "scheduler.wsgi.application"


def _database_from_url(url: str) -> dict:
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").split("+")[0]
    if scheme in ("postgres", "postgresql"):
        # Decode %xx in userinfo (e.g. passwords containing @ or :).
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote((parsed.path or "").lstrip("/")) or "",
            "USER": unquote(parsed.username) if parsed.username else "",
            "PASSWORD": unquote(parsed.password) if parsed.password else "",
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or 5432),
        }
    if scheme == "sqlite":
        path = parsed.path or ""
        if path == ":memory:":
            return {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
        # sqlite:///relative/path
        name = path.lstrip("/")
        if not parsed.netloc:
            return {"ENGINE": "django.db.backends.sqlite3", "NAME": name or str(BASE_DIR / "db.sqlite3")}
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": f"//{parsed.netloc}{path}"}
    raise ValueError(f"Unsupported DATABASE_URL scheme: {scheme}")


DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL:
    DATABASES = {"default": _database_from_url(DATABASE_URL)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- App-specific ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

RETELL_API_KEY = os.environ.get("RETELL_API_KEY", "")

CALCOM_API_KEY = os.environ.get("CALCOM_API_KEY", "")
# Bookings create/list/reschedule/cancel use 2026-02-25 per Cal.com docs (March 2026).
CALCOM_BOOKINGS_API_VERSION = os.environ.get("CALCOM_BOOKINGS_API_VERSION", "2026-02-25")
# Slots endpoint requires 2024-09-04 per Cal.com docs.
CALCOM_SLOTS_API_VERSION = os.environ.get("CALCOM_SLOTS_API_VERSION", "2024-09-04")
# GET /event-types per Cal.com docs (default 2024-06-14).
CALCOM_EVENT_TYPES_API_VERSION = os.environ.get("CALCOM_EVENT_TYPES_API_VERSION", "2024-06-14")
# Backwards-compatible alias used in .env.example
CALCOM_API_VERSION = os.environ.get("CALCOM_API_VERSION", CALCOM_BOOKINGS_API_VERSION)

CALCOM_BASE_URL = os.environ.get("CALCOM_BASE_URL", "https://api.cal.com/v2").rstrip("/")
CALCOM_REQUEST_TIMEOUT = float(os.environ.get("CALCOM_REQUEST_TIMEOUT", "30"))
CALCOM_MAX_RETRIES = int(os.environ.get("CALCOM_MAX_RETRIES", "2"))

DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "America/Los_Angeles")

# Multi-service catalog: service_key -> { event_type_id, label, description }
# Override entirely via SERVICE_CATALOG_JSON (JSON object). Keys must be strings.
# Handyman catalog: only ``repair_request`` and ``repair_estimate`` (override IDs via
# ``SERVICE_CATALOG_JSON``).
_DEFAULT_SERVICE_CATALOG: dict = {
    "repair_request": {
        "event_type_id": 1,
        "label": "Repair request",
        "description": "On-site repair / intake — set event_type_id via SERVICE_CATALOG_JSON.",
    },
    "repair_estimate": {
        "event_type_id": 123,
        "label": "Repair estimate",
        "description": "Repair estimate visit — set event_type_id via SERVICE_CATALOG_JSON.",
    },
}
_SERVICE_CATALOG_JSON = os.environ.get("SERVICE_CATALOG_JSON", "").strip()
if _SERVICE_CATALOG_JSON:
    SERVICE_CATALOG = json.loads(_SERVICE_CATALOG_JSON)
    if not isinstance(SERVICE_CATALOG, dict):
        raise ValueError("SERVICE_CATALOG_JSON must be a JSON object")
    for _k, _v in SERVICE_CATALOG.items():
        if not isinstance(_v, dict) or "event_type_id" not in _v:
            raise ValueError(
                f"SERVICE_CATALOG_JSON entry {_k!r} must be an object with event_type_id"
            )
        _dmo = _v.get("duration_minutes_options")
        if _dmo is not None:
            if not isinstance(_dmo, list) or not _dmo:
                raise ValueError(
                    f"SERVICE_CATALOG_JSON entry {_k!r}: duration_minutes_options must be a non-empty array of integers"
                )
            for _x in _dmo:
                if isinstance(_x, bool) or not isinstance(_x, int) or _x < 1:
                    raise ValueError(
                        f"SERVICE_CATALOG_JSON entry {_k!r}: duration_minutes_options must contain positive integers only"
                    )
        _olm = _v.get("omit_length_in_minutes")
        if _olm is not None and not isinstance(_olm, bool):
            raise ValueError(
                f"SERVICE_CATALOG_JSON entry {_k!r}: omit_length_in_minutes must be a boolean if set"
            )
else:
    SERVICE_CATALOG = _DEFAULT_SERVICE_CATALOG

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "jsonish": {
            "()": "booking.utils.logging.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": "jsonish",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {"level": LOG_LEVEL},
        "django.request": {"level": LOG_LEVEL},
        "booking": {"level": LOG_LEVEL},
        "httpx": {"level": "WARNING"},
    },
}
