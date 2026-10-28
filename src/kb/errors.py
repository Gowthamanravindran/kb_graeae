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
