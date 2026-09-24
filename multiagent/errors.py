class MultiAgentError(Exception):
    """Base error for the multi-agent backend."""


class ProviderUnavailable(MultiAgentError):
    pass


class AuthenticationError(MultiAgentError):
    pass


class ModelUnavailable(MultiAgentError):
    pass


class QuotaExceeded(MultiAgentError):
    pass


class RateLimitExceeded(MultiAgentError):
    pass


class ProviderRequestError(MultiAgentError):
    """The provider responded but rejected the request or payload."""


class StructuredOutputError(MultiAgentError):
    pass


class ContextBudgetError(MultiAgentError):
    pass


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
