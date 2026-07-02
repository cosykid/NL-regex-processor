"""Typed errors for the processing pipeline.

The Celery task uses these to decide whether a failure is worth *retrying*
(transient: network blip talking to the LLM, Redis hiccup) or should fail the
job immediately (permanent: the user asked for an impossible column, the
generated regex is unsafe).
"""


class ProcessingError(Exception):
    """Base class for all pipeline errors."""


class TransientError(ProcessingError):
    """A failure that may succeed on retry (network, broker, backend)."""


class LLMError(TransientError):
    """The LLM call failed (network / API error). Retryable."""


class RegexGenerationError(ProcessingError):
    """No regex could be derived from the prompt (permanent)."""


class UnsafeRegexError(ProcessingError):
    """The generated regex failed validation / safety checks (permanent)."""


class JobCancelled(ProcessingError):
    """The job was cancelled by the user mid-flight."""


class SparkProcessingError(ProcessingError):
    """The Spark transformation failed (permanent)."""
