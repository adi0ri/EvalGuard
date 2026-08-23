"""Deterministic resilience primitives shared by providers and workers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import NoReturn

from rq.timeouts import JobTimeoutException


class FaultMode(StrEnum):
    """Stable identifiers for synthetic execution fault injection."""

    TRANSIENT_BEFORE_PROVIDER = "transient_before_provider"
    TRANSIENT_AFTER_TOOLS = "transient_after_tools"
    TIMEOUT = "timeout"
    PERMANENT_BEFORE_PROVIDER = "permanent_before_provider"
    WORKER_INTERRUPTION = "worker_interruption"


class FailureKind(StrEnum):
    """Stable, persistence-safe failure classifications."""

    INJECTED_TRANSIENT = "injected_transient"
    TIMEOUT = "timeout"
    INJECTED_PERMANENT = "injected_permanent"
    MAXIMUM_TOOL_STEPS = "maximum_tool_steps"
    WORKER_INTERRUPTION = "worker_interruption"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class FaultClassification:
    """Say whether one failure is retryable and give it a stable kind."""

    kind: str
    retryable: bool


class InjectedRetryableError(RuntimeError):
    """A deterministic synthetic failure that may be retried."""

    def __init__(
        self,
        mode: FaultMode,
        attempt_number: int,
        *,
        steps_attempted: int | None = None,
    ) -> None:
        self.mode = mode
        self.attempt_number = attempt_number
        self.steps_attempted = steps_attempted
        super().__init__(f"Injected retryable fault {mode.value} on attempt {attempt_number}")


class InjectedTimeoutError(InjectedRetryableError):
    """A synthetic timeout raised when RQ does not interrupt the sleeper first."""


class InjectedPermanentError(RuntimeError):
    """A deterministic synthetic failure that must not be retried."""

    def __init__(self, mode: FaultMode, attempt_number: int) -> None:
        self.mode = mode
        self.attempt_number = attempt_number
        super().__init__(f"Injected permanent fault {mode.value} on attempt {attempt_number}")


class MaximumToolStepsExceeded(RuntimeError):
    """Raised before a tool call that would exceed its execution budget."""

    def __init__(self, *, limit: int, attempted_steps: int) -> None:
        self.limit = limit
        self.attempted_steps = attempted_steps
        super().__init__(
            f"Maximum tool steps exceeded: limit={limit}, attempted_steps={attempted_steps}"
        )


class InjectedWorkerInterruption(SystemExit):
    """A synthetic non-Exception interruption analogous to worker termination."""

    def __init__(self, *, attempt_number: int) -> None:
        self.mode = FaultMode.WORKER_INTERRUPTION
        self.attempt_number = attempt_number
        super().__init__(f"Injected worker interruption on attempt {attempt_number}")


@dataclass(slots=True)
class StepBudget:
    """Count attempted tool calls and reject the first call beyond the limit."""

    limit: int
    _attempted_steps: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("Tool step limit must be at least 1")

    @property
    def attempted_steps(self) -> int:
        return self._attempted_steps

    def consume(self) -> int:
        """Consume and return one attempted step, even when it exceeds the limit."""

        self._attempted_steps += 1
        if self._attempted_steps > self.limit:
            raise MaximumToolStepsExceeded(
                limit=self.limit,
                attempted_steps=self._attempted_steps,
            )
        return self._attempted_steps


def classify_failure(error: BaseException) -> FaultClassification:
    """Classify known failures conservatively; unexpected failures are permanent."""

    if isinstance(error, (JobTimeoutException, InjectedTimeoutError)):
        return FaultClassification(kind=FailureKind.TIMEOUT.value, retryable=True)
    if isinstance(error, InjectedRetryableError):
        return FaultClassification(
            kind=FailureKind.INJECTED_TRANSIENT.value,
            retryable=True,
        )
    if isinstance(error, InjectedWorkerInterruption):
        return FaultClassification(
            kind=FailureKind.WORKER_INTERRUPTION.value,
            retryable=True,
        )
    if isinstance(error, MaximumToolStepsExceeded):
        return FaultClassification(
            kind=FailureKind.MAXIMUM_TOOL_STEPS.value,
            retryable=False,
        )
    if isinstance(error, InjectedPermanentError):
        return FaultClassification(
            kind=FailureKind.INJECTED_PERMANENT.value,
            retryable=False,
        )
    return FaultClassification(kind=FailureKind.UNEXPECTED.value, retryable=False)


def inject_timeout_fault(*, attempt_number: int, timeout_seconds: int) -> NoReturn:
    """Sleep past the RQ deadline, then raise if no worker timer interrupts first."""

    if timeout_seconds < 0:
        raise ValueError("Timeout seconds cannot be negative")
    time.sleep(timeout_seconds + 0.25)
    raise InjectedTimeoutError(FaultMode.TIMEOUT, attempt_number)


def _should_inject(*, attempt_number: int, fail_attempts: int) -> bool:
    if attempt_number < 1:
        raise ValueError("Attempt number must be at least 1")
    if fail_attempts < 0:
        raise ValueError("Fail attempts cannot be negative")
    return attempt_number <= fail_attempts


def inject_before_provider(
    *,
    mode: str,
    attempt_number: int,
    fail_attempts: int,
    timeout_seconds: int,
) -> None:
    """Inject a configured pre-provider fault without mutable process state."""

    fault_mode = FaultMode(mode)
    if not _should_inject(
        attempt_number=attempt_number,
        fail_attempts=fail_attempts,
    ):
        return
    if fault_mode is FaultMode.TRANSIENT_BEFORE_PROVIDER:
        raise InjectedRetryableError(fault_mode, attempt_number)
    if fault_mode is FaultMode.TIMEOUT:
        inject_timeout_fault(
            attempt_number=attempt_number,
            timeout_seconds=timeout_seconds,
        )
    if fault_mode is FaultMode.PERMANENT_BEFORE_PROVIDER:
        raise InjectedPermanentError(fault_mode, attempt_number)
    if fault_mode is FaultMode.WORKER_INTERRUPTION:
        raise InjectedWorkerInterruption(attempt_number=attempt_number)


def inject_after_tools(
    *,
    mode: str,
    attempt_number: int,
    fail_attempts: int,
    steps_attempted: int,
) -> None:
    """Inject a configured post-tool fault without mutable process state."""

    if steps_attempted < 0:
        raise ValueError("Attempted steps cannot be negative")
    fault_mode = FaultMode(mode)
    if not _should_inject(
        attempt_number=attempt_number,
        fail_attempts=fail_attempts,
    ):
        return
    if fault_mode is FaultMode.TRANSIENT_AFTER_TOOLS:
        raise InjectedRetryableError(
            fault_mode,
            attempt_number,
            steps_attempted=steps_attempted,
        )
