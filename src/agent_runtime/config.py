"""Load and validate config.yaml, resolve the model string.

Model precedence: AGENT_MODEL env > CLAUDE_MODEL env (the fleet-wide
Actions variable from v1) > config.yaml `model` > DEFAULT_MODEL. A model
string is `provider/model-id`; no slash means anthropic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .errors import ConfigError

DEFAULT_MODEL = "anthropic/claude-sonnet-5-5"

# Paths the agent can never modify or delete, whatever config says. These
# are the things that decide what the agent is and how it runs; an agent
# that can rewrite them can rewrite its own guard rails.
HARD_PROTECTED = (
    "MISSION.md",
    "config.yaml",
    "requirements.txt",
    "log/cycle-log.md",
    ".github/",
    ".git/",
    "webhook/",
)


@dataclass
class EmailConfig:
    enabled: bool = False
    from_address: str = ""
    from_name: str = ""
    to_address: str = ""
    reply_to: str = ""
    review_required: bool = True


@dataclass
class Config:
    name: str
    model: str
    max_tokens: int = 16000
    max_tool_rounds: int = 25
    max_log_entries: int = 20
    email: EmailConfig = field(default_factory=EmailConfig)
    protected_paths: tuple[str, ...] = HARD_PROTECTED

    @property
    def provider(self) -> str:
        return self.model.split("/", 1)[0]

    @property
    def model_id(self) -> str:
        return self.model.split("/", 1)[1]


def resolve_model(config_value: str | None, env: dict[str, str] | None = None) -> str:
    """Return the `provider/model` string the cycle will use."""
    env = os.environ if env is None else env
    raw = env.get("AGENT_MODEL") or env.get("CLAUDE_MODEL") or config_value or DEFAULT_MODEL
    raw = raw.strip()
    if not raw:
        raise ConfigError("model resolved to an empty string")
    if "/" not in raw:
        raw = f"anthropic/{raw}"
    provider, model_id = raw.split("/", 1)
    if not provider or not model_id:
        raise ConfigError(f"model must be 'provider/model-id', got {raw!r}")
    return raw


def _require_int(data: dict, key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{key} must be a positive integer, got {value!r}")
    return value


def load_config(path: Path, env: dict[str, str] | None = None) -> Config:
    """Read config.yaml. Missing or unreadable is a failure, not a default:
    a fork without a config is misconfigured and should say so."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{path} not found; the template ships one")
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a mapping at the top level")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("config.yaml needs a non-empty `name`")

    email_raw = (data.get("capabilities") or {}).get("email") or {}
    if not isinstance(email_raw, dict):
        raise ConfigError("capabilities.email must be a mapping")
    email = EmailConfig(
        enabled=bool(email_raw.get("enabled", False)),
        from_address=str(email_raw.get("from_address") or ""),
        from_name=str(email_raw.get("from_name") or ""),
        to_address=str(email_raw.get("to_address") or ""),
        reply_to=str(email_raw.get("reply_to") or ""),
        review_required=bool(email_raw.get("review_required", True)),
    )
    if email.enabled and (not email.from_address or not email.to_address):
        raise ConfigError("email is enabled but from_address or to_address is empty")

    extra = data.get("protected_paths") or []
    if not isinstance(extra, list) or not all(isinstance(p, str) for p in extra):
        raise ConfigError("protected_paths must be a list of strings")

    return Config(
        name=name.strip(),
        model=resolve_model(data.get("model"), env),
        max_tokens=_require_int(data, "max_tokens", 16000),
        max_tool_rounds=_require_int(data, "max_tool_rounds", 25),
        max_log_entries=_require_int(data, "max_log_entries", 20),
        email=email,
        protected_paths=tuple(HARD_PROTECTED) + tuple(p.strip() for p in extra if p.strip()),
    )
