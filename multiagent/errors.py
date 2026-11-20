from datetime import datetime


class MultiAgentError(Exception):
    """Base error for the multi-agent backend."""


class ProviderUnavailable(MultiAgentError):
    def __init__(self, message: str, *, retry_at: datetime | None = None):
        super().__init__(message)
        self.retry_at = retry_at


class AuthenticationError(MultiAgentError):
    pass


class ModelUnavailable(MultiAgentError):
    pass


class QuotaExceeded(MultiAgentError):
    def __init__(self, message: str, *, retry_at: datetime | None = None):
        super().__init__(message)
        self.retry_at = retry_at


class RateLimitExceeded(MultiAgentError):
    def __init__(self, message: str, *, retry_at: datetime | None = None):
        super().__init__(message)
        self.retry_at = retry_at


class ProviderRequestError(MultiAgentError):
    """The provider responded but rejected the request or payload."""


class StructuredOutputError(MultiAgentError):
    pass


class ContextBudgetError(MultiAgentError):
    pass


class EscalationBlocked(MultiAgentError):
    """A step failed on the quality of its output and cannot be escalated (ECO-T06).

    The message says why (ceiling, no eligible model at the next level, an exhausted budget, ...) and,
    where only a person can unblock the step, asks for human intervention. ``code`` is the stable reason
    recorded in the ``escalation_blocked`` run event.
    """

    def __init__(self, message: str, *, code: str = "escalation_blocked", reason_codes: tuple[str, ...] = ()):
        super().__init__(message)
        self.code = code
        self.reason_codes = tuple(reason_codes)


class BudgetExceeded(MultiAgentError):
    pass


class InvalidStateTransition(MultiAgentError):
    pass


class IdempotencyConflict(MultiAgentError):
    pass


class ValidationFailed(MultiAgentError):
    pass


class HumanSubmissionError(MultiAgentError):
    pass
