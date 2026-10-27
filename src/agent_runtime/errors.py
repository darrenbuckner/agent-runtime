"""Exception types. Every failure in the runtime raises one of these or a
subclass; nothing returns None, "", or [] to mean "it broke"."""


class AgentError(Exception):
    """Base class for runtime failures."""


class ConfigError(AgentError):
    """config.yaml missing, unreadable, or invalid."""


class WorkspaceError(AgentError):
    """A path was rejected or a file operation failed for a reason other
    than the file not existing."""


class ProtectedPathError(WorkspaceError):
    """The agent tried to write or delete a path it is not allowed to."""


class ProviderError(AgentError):
    """The LLM provider could not be built or returned something the loop
    cannot continue from."""


class CycleStopped(AgentError):
    """The model stopped for a reason other than finishing its turn or
    calling a tool (refusal, max_tokens, incomplete). Carries the detail
    the cycle log needs."""


class GitError(AgentError):
    """Commit or push failed after retries."""


class ApiError(AgentError):
    """A GitHub or Resend call returned an unexpected status."""
