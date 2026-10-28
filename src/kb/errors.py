class KbError(Exception):
    """Base error; the CLI prints its message without a traceback."""


class ConfigError(KbError):
    pass


class LLMUnavailable(KbError):
    pass


class LLMOutputError(KbError):
    pass
