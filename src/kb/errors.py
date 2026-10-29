class KbError(Exception):
    """Base error; the CLI prints its message without a traceback."""


class ConfigError(KbError):
    pass


class LLMUnavailable(KbError):
    pass


class LLMOutputError(KbError):
    pass


class LLMCallError(LLMOutputError):
    """One call failed (max turns, persistent overload, API error); the run can continue without it."""


class RegistryError(KbError):
    def __init__(self, message: str, status: int | None = None, body: dict | None = None):
        super().__init__(message)
        self.status = status
        self.body = body or {}


class RegistryUnavailable(RegistryError):
    pass
