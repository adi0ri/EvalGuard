"""Deterministic experiment summaries and comparable prompt-run analysis."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from math import ceil
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from evalguard.db.models import (
    AgentPrompt,
    CaseExecution,
    EvaluationCase,
    EvaluationSuite,
    ExecutionFaultPlan,
    Experiment,
    GraderResult,
    ProviderTurn,
    TraceEvent,
)
from evalguard.providers.configuration import OPENAI_PROVIDER

TERMINAL_EXECUTION_STATUSES = frozenset({"completed", "failed", "enqueue_failed"})
StabilityStatus = Literal["stable_pass", "stable_fail", "unstable", "incomplete"]
ComparisonStatus = Literal["improved", "regressed", "unchanged", "provisional"]


class ExperimentAnalysisNotFoundError(LookupError):
    pass


class ExperimentComparisonConflictError(ValueError):
    pass


@dataclass(frozen=True)
class Ratio:
    numerator: int
    denominator: int
    value: float | None


@dataclass(frozen=True)
class PromptIdentity:
    name: str
    version: str
    definition_hash: str


@dataclass(frozen=True)
class ExecutionCounts:
    expected: int
    terminal: int
    completed: int
    infrastructure_failed: int
    nonterminal: int
    graded: int
    passing: int
    criterion_failing: int
    ungraded_terminal: int


@dataclass(frozen=True)
class DurationMetric:
    observed_count: int
    minimum_ms: int | None
    mean_ms: int | None
    median_ms: int | None
    p95_ms: int | None
    maximum_ms: int | None


@dataclass(frozen=True)
class TimingAnalysis:
    label: str
    queue_delay: DurationMetric
    run_duration: DurationMetric
    end_to_end_duration: DurationMetric


@dataclass(frozen=True)
class TrialAnalysis:
    execution_id: UUID
    trial_number: int
    status: str
    attempt_count: int
    quality_passed: bool | None
    grader_criteria: int
    passed_criteria: int
    queue_delay_ms: int | None
    run_duration_ms: int | None
    end_to_end_duration_ms: int | None


@dataclass(frozen=True)
class CaseAnalysis:
    case_id: UUID
    case_key: str
    category: str
    expected_trials: int
    terminal_trials: int
    graded_trials: int
    passing_trials: int
    strict_pass_rate: Ratio
    stability: StabilityStatus
    trials: tuple[TrialAnalysis, ...]


@dataclass(frozen=True)
class GraderFamilyAnalysis:
    grader_name: str
    passed_criteria: int
    total_criteria: int
    pass_rate: Ratio


@dataclass(frozen=True)
class CriterionAnalysis:
    grader_name: str
    criterion: str
    passed: int
    total: int
    pass_rate: Ratio


@dataclass(frozen=True)
class CategoryAnalysis:
    category: str
    expected_executions: int
    terminal_executions: int
    graded_executions: int
    passing_executions: int
    strict_pass_rate: Ratio
    grading_coverage: Ratio


@dataclass(frozen=True)
class StabilityCounts:
    stable_pass: int
    stable_fail: int
    unstable: int
    incomplete: int


@dataclass(frozen=True)
class GridIntegrity:
    expected_coordinates: int
    observed_executions: int
    missing_coordinates: int
    unexpected_coordinates: int
    duplicate_coordinates: int
    valid: bool


@dataclass(frozen=True)
class ProviderUsage:
    request_count: int
    response_count: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    estimated_cost_usd: str | None
    returned_models: tuple[str, ...]
    latency: DurationMetric


@dataclass(frozen=True)
class ExperimentAnalysis:
    experiment_id: UUID
    status: str
    suite_id: UUID
    suite_name: str
    suite_version: str
    suite_definition_hash: str
    prompt: PromptIdentity | None
    prompt_is_published: bool | None
    provider: str
    provider_model: str | None
    provider_config_hash: str | None
    trials: int
    max_attempts: int
    job_timeout_seconds: int
    max_tool_steps: int
    retry_backoff_seconds: int
    created_at: datetime
    completed_at: datetime | None
    result_label: str
    is_terminal: bool
    is_fully_graded: bool
    quality_comparison_ready: bool
    token_usage_available: bool
    cost_available: bool
    provider_usage: ProviderUsage | None
    fault_injection_present: bool
    grid_integrity: GridIntegrity
    execution_counts: ExecutionCounts
    status_counts: dict[str, int]
    strict_pass_rate: Ratio
    graded_pass_rate: Ratio
    grading_coverage: Ratio
    criterion_pass_rate: Ratio
    total_attempts: int
    retry_attempts: int
    stability_counts: StabilityCounts
    stable_case_rate: Ratio
    timing: TimingAnalysis
    grader_families: tuple[GraderFamilyAnalysis, ...]
    criteria: tuple[CriterionAnalysis, ...]
    categories: tuple[CategoryAnalysis, ...]
    cases: tuple[CaseAnalysis, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValueDelta:
    baseline: float | int | None
    candidate: float | int | None
    delta: float | int | None


@dataclass(frozen=True)
class CaseComparison:
    case_key: str
    category: str
    baseline_pass_rate: Ratio
    candidate_pass_rate: Ratio
    pass_rate_delta: float | None
    classification: ComparisonStatus


@dataclass(frozen=True)
class GraderFamilyComparison:
    grader_name: str
    baseline_pass_rate: Ratio
    candidate_pass_rate: Ratio
    pass_rate_delta: float | None


@dataclass(frozen=True)
class ExperimentComparison:
    baseline: ExperimentAnalysis
    candidate: ExperimentAnalysis
    quality_comparison_ready: bool
    strict_pass_rate: ValueDelta
    criterion_pass_rate: ValueDelta
    stable_case_rate: ValueDelta
    mean_run_duration_ms: ValueDelta
    total_attempts: ValueDelta
    grader_families: tuple[GraderFamilyComparison, ...]
    cases: tuple[CaseComparison, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _ExecutionEvidence:
    execution: CaseExecution
    case: EvaluationCase
    graders: list[GraderResult]
    fault_plan: ExecutionFaultPlan | None
    grader_completion_payloads: tuple[Any, ...]


@dataclass(frozen=True)
class _ExperimentRows:
    experiment: Experiment
    suite: EvaluationSuite
    prompt: AgentPrompt | None
    expected_cases: tuple[EvaluationCase, ...]
    executions: tuple[_ExecutionEvidence, ...]
    provider_turns: tuple[ProviderTurn, ...]


def _ratio(numerator: int, denominator: int) -> Ratio:
    value = None if denominator == 0 else round(numerator / denominator, 6)
    return Ratio(numerator=numerator, denominator=denominator, value=value)


def _milliseconds(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _duration_metric(values: list[int]) -> DurationMetric:
    if not values:
        return DurationMetric(
            observed_count=0,
            minimum_ms=None,
            mean_ms=None,
            median_ms=None,
            p95_ms=None,
            maximum_ms=None,
        )
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[midpoint]
    else:
        median = round((ordered[midpoint - 1] + ordered[midpoint]) / 2)
    return DurationMetric(
        observed_count=len(values),
        minimum_ms=ordered[0],
        mean_ms=round(sum(values) / len(values)),
        median_ms=median,
        p95_ms=ordered[ceil(0.95 * len(ordered)) - 1],
        maximum_ms=ordered[-1],
    )


def _load_experiment_rows(
    session: Session,
    experiment_ids: tuple[UUID, ...],
) -> dict[UUID, _ExperimentRows]:
    """Load requested evidence together, then read immutable suite membership."""

    completion_execution = aliased(CaseExecution, name="completion_execution")
    grader_completions = (
        select(
            TraceEvent.case_execution_id.label("case_execution_id"),
            func.jsonb_agg(TraceEvent.payload).label("payloads"),
        )
        .join(completion_execution, completion_execution.id == TraceEvent.case_execution_id)
        .where(
            completion_execution.experiment_id.in_(experiment_ids),
            TraceEvent.event_type == "grader_completed",
        )
        .group_by(TraceEvent.case_execution_id)
        .subquery()
    )
    rows = session.execute(
        select(
            Experiment,
            EvaluationSuite,
            AgentPrompt,
            CaseExecution,
            EvaluationCase,
            GraderResult,
            ExecutionFaultPlan,
            grader_completions.c.payloads,
        )
        .join(EvaluationSuite, EvaluationSuite.id == Experiment.suite_id)
        .outerjoin(AgentPrompt, AgentPrompt.id == Experiment.agent_prompt_id)
        .join(CaseExecution, CaseExecution.experiment_id == Experiment.id)
        .join(EvaluationCase, EvaluationCase.id == CaseExecution.case_id)
        .outerjoin(GraderResult, GraderResult.case_execution_id == CaseExecution.id)
        .outerjoin(
            ExecutionFaultPlan,
            ExecutionFaultPlan.case_execution_id == CaseExecution.id,
        )
        .outerjoin(
            grader_completions,
            grader_completions.c.case_execution_id == CaseExecution.id,
        )
        .where(Experiment.id.in_(experiment_ids))
        .order_by(
            Experiment.id,
            EvaluationCase.key,
            CaseExecution.trial_number,
            GraderResult.grader_name,
            GraderResult.criterion,
        )
    ).all()

    grouped: dict[UUID, dict[UUID, _ExecutionEvidence]] = defaultdict(dict)
    headers: dict[UUID, tuple[Experiment, EvaluationSuite, AgentPrompt | None]] = {}
    for (
        experiment,
        suite,
        prompt,
        execution,
        case,
        grader,
        fault_plan,
        completion_payloads,
    ) in rows:
        headers[experiment.id] = (experiment, suite, prompt)
        payloads = () if completion_payloads is None else tuple(completion_payloads)
        evidence = grouped[experiment.id].setdefault(
            execution.id,
            _ExecutionEvidence(
                execution=execution,
                case=case,
                graders=[],
                fault_plan=fault_plan,
                grader_completion_payloads=payloads,
            ),
        )
        if grader is not None:
            evidence.graders.append(grader)

    loaded: dict[UUID, _ExperimentRows] = {}
    provider_turns_by_experiment: dict[UUID, list[ProviderTurn]] = defaultdict(list)
    if headers:
        provider_turn_rows = session.execute(
            select(CaseExecution.experiment_id, ProviderTurn)
            .join(ProviderTurn, ProviderTurn.case_execution_id == CaseExecution.id)
            .where(CaseExecution.experiment_id.in_(experiment_ids))
            .order_by(
                CaseExecution.experiment_id,
                ProviderTurn.case_execution_id,
                ProviderTurn.sequence_number,
            )
        ).all()
        for turn_experiment_id, provider_turn in provider_turn_rows:
            provider_turns_by_experiment[turn_experiment_id].append(provider_turn)
    expected_cases_by_suite: dict[UUID, list[EvaluationCase]] = defaultdict(list)
    if headers:
        suite_ids = {suite.id for _, suite, _ in headers.values()}
        expected_cases = session.scalars(
            select(EvaluationCase)
            .where(EvaluationCase.suite_id.in_(suite_ids))
            .order_by(EvaluationCase.suite_id, EvaluationCase.key)
        ).all()
        for case in expected_cases:
            expected_cases_by_suite[case.suite_id].append(case)
    for experiment_id, header in headers.items():
        experiment, suite, prompt = header
        loaded[experiment_id] = _ExperimentRows(
            experiment=experiment,
            suite=suite,
            prompt=prompt,
            expected_cases=tuple(expected_cases_by_suite[suite.id]),
            executions=tuple(grouped[experiment_id].values()),
            provider_turns=tuple(provider_turns_by_experiment[experiment_id]),
        )
    return loaded


def _quality_signature(
    evidence: _ExecutionEvidence,
) -> tuple[tuple[str, str, bool], ...] | None:
    if (
        evidence.execution.status != "completed"
        or not evidence.graders
        or len(evidence.grader_completion_payloads) != 1
    ):
        return None

    completion = evidence.grader_completion_payloads[0]
    if not isinstance(completion, dict):
        return None
    raw_criteria = completion.get("criteria")
    raw_grader_names = completion.get("grader_names")
    recorded_passed = completion.get("passed")
    if (
        not isinstance(raw_criteria, list)
        or not raw_criteria
        or not isinstance(raw_grader_names, list)
        or not isinstance(recorded_passed, bool)
    ):
        return None

    marker_rows: list[tuple[str, str, bool]] = []
    for row in raw_criteria:
        if not isinstance(row, dict):
            return None
        grader_name = row.get("grader_name")
        criterion = row.get("criterion")
        passed = row.get("passed")
        if not isinstance(grader_name, str) or not isinstance(criterion, str):
            return None
        if not grader_name or not criterion or not isinstance(passed, bool):
            return None
        marker_rows.append((grader_name, criterion, passed))

    persisted_signature = tuple(
        sorted((grader.grader_name, grader.criterion, grader.passed) for grader in evidence.graders)
    )
    marker_signature = tuple(sorted(marker_rows))
    expected_grader_names = sorted({row[0] for row in marker_signature})
    if (
        marker_signature != persisted_signature
        or raw_grader_names != expected_grader_names
        or recorded_passed != all(row[2] for row in marker_signature)
    ):
        return None
    return persisted_signature


def _quality_outcome(evidence: _ExecutionEvidence) -> bool | None:
    signature = _quality_signature(evidence)
    if signature is None:
        return None
    return all(row[2] for row in signature)


def _stability(
    signatures: list[tuple[tuple[str, str, bool], ...] | None],
    expected_trials: int,
) -> StabilityStatus:
    if len(signatures) != expected_trials or any(signature is None for signature in signatures):
        return "incomplete"
    concrete = [signature for signature in signatures if signature is not None]
    if any(signature != concrete[0] for signature in concrete[1:]):
        return "unstable"
    return "stable_pass" if all(row[2] for row in concrete[0]) else "stable_fail"


def _analyze_rows(rows: _ExperimentRows) -> ExperimentAnalysis:
    experiment = rows.experiment
    suite = rows.suite
    prompt = rows.prompt
    expected_cases_by_id = {case.id: case for case in rows.expected_cases}
    expected_coordinates = {
        (case.id, trial_number)
        for case in rows.expected_cases
        for trial_number in range(1, experiment.trials + 1)
    }
    coordinate_counts = Counter(
        (evidence.execution.case_id, evidence.execution.trial_number)
        for evidence in rows.executions
    )
    observed_coordinates = set(coordinate_counts)
    missing_coordinates = expected_coordinates.difference(observed_coordinates)
    unexpected_coordinates = observed_coordinates.difference(expected_coordinates)
    duplicate_coordinates = sum(count - 1 for count in coordinate_counts.values() if count > 1)
    grid_integrity = GridIntegrity(
        expected_coordinates=len(expected_coordinates),
        observed_executions=len(rows.executions),
        missing_coordinates=len(missing_coordinates),
        unexpected_coordinates=len(unexpected_coordinates),
        duplicate_coordinates=duplicate_coordinates,
        valid=(
            not missing_coordinates and not unexpected_coordinates and duplicate_coordinates == 0
        ),
    )

    evidence_by_coordinate: dict[tuple[UUID, int], _ExecutionEvidence] = {}
    for evidence in rows.executions:
        coordinate = (evidence.execution.case_id, evidence.execution.trial_number)
        if coordinate in expected_coordinates and coordinate not in evidence_by_coordinate:
            evidence_by_coordinate[coordinate] = evidence
    expected_evidence = tuple(evidence_by_coordinate.values())

    status_counts = Counter(evidence.execution.status for evidence in expected_evidence)
    family_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    criterion_counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    category_evidence: dict[str, list[_ExecutionEvidence]] = defaultdict(list)
    category_case_ids: dict[str, set[UUID]] = defaultdict(set)
    case_evidence: dict[tuple[UUID, str, str], list[_ExecutionEvidence]] = {}
    for case in rows.expected_cases:
        category = str(case.expected.get("category", "legacy"))
        case_evidence[(case.id, case.key, category)] = []
        category_case_ids[category].add(case.id)
    queue_durations: list[int] = []
    run_durations: list[int] = []
    end_to_end_durations: list[int] = []

    passing = 0
    graded = 0
    criterion_failing = 0
    ungraded_terminal = 0
    passed_criteria = 0
    total_criteria = 0
    total_attempts = 0

    for evidence in expected_evidence:
        execution = evidence.execution
        expected_case = expected_cases_by_id[execution.case_id]
        category = str(expected_case.expected.get("category", "legacy"))
        category_evidence[category].append(evidence)
        case_evidence[(expected_case.id, expected_case.key, category)].append(evidence)
        total_attempts += execution.attempt_count

        queue_delay = _milliseconds(execution.created_at, execution.started_at)
        run_duration = _milliseconds(execution.started_at, execution.completed_at)
        end_to_end = _milliseconds(execution.created_at, execution.completed_at)
        if queue_delay is not None:
            queue_durations.append(queue_delay)
        if run_duration is not None:
            run_durations.append(run_duration)
        if end_to_end is not None:
            end_to_end_durations.append(end_to_end)

        outcome = _quality_outcome(evidence)
        if outcome is not None:
            graded += 1
        elif execution.status in TERMINAL_EXECUTION_STATUSES:
            ungraded_terminal += 1
        if outcome is True:
            passing += 1
        elif outcome is False:
            criterion_failing += 1

        for grader in evidence.graders if outcome is not None else ():
            family = family_counts[grader.grader_name]
            criterion = criterion_counts[(grader.grader_name, grader.criterion)]
            family[1] += 1
            criterion[1] += 1
            total_criteria += 1
            if grader.passed:
                family[0] += 1
                criterion[0] += 1
                passed_criteria += 1

    case_analyses: list[CaseAnalysis] = []
    for (case_id, case_key, category), evidences in sorted(
        case_evidence.items(), key=lambda item: item[0][1]
    ):
        trial_rows: list[TrialAnalysis] = []
        outcomes: list[bool | None] = []
        signatures: list[tuple[tuple[str, str, bool], ...] | None] = []
        for evidence in sorted(evidences, key=lambda item: item.execution.trial_number):
            execution = evidence.execution
            outcome = _quality_outcome(evidence)
            outcomes.append(outcome)
            signatures.append(_quality_signature(evidence))
            trial_rows.append(
                TrialAnalysis(
                    execution_id=execution.id,
                    trial_number=execution.trial_number,
                    status=execution.status,
                    attempt_count=execution.attempt_count,
                    quality_passed=outcome,
                    grader_criteria=len(evidence.graders),
                    passed_criteria=sum(grader.passed for grader in evidence.graders),
                    queue_delay_ms=_milliseconds(execution.created_at, execution.started_at),
                    run_duration_ms=_milliseconds(execution.started_at, execution.completed_at),
                    end_to_end_duration_ms=_milliseconds(
                        execution.created_at, execution.completed_at
                    ),
                )
            )
        passing_trials = sum(outcome is True for outcome in outcomes)
        case_analyses.append(
            CaseAnalysis(
                case_id=case_id,
                case_key=case_key,
                category=category,
                expected_trials=experiment.trials,
                terminal_trials=sum(
                    evidence.execution.status in TERMINAL_EXECUTION_STATUSES
                    for evidence in evidences
                ),
                graded_trials=sum(_quality_outcome(evidence) is not None for evidence in evidences),
                passing_trials=passing_trials,
                strict_pass_rate=_ratio(passing_trials, experiment.trials),
                stability=_stability(signatures, experiment.trials),
                trials=tuple(trial_rows),
            )
        )

    stability_counter = Counter(row.stability for row in case_analyses)
    category_rows: list[CategoryAnalysis] = []
    for category, case_ids in sorted(category_case_ids.items()):
        evidences = category_evidence[category]
        category_passing = sum(_quality_outcome(evidence) is True for evidence in evidences)
        category_graded = sum(_quality_outcome(evidence) is not None for evidence in evidences)
        category_expected = len(case_ids) * experiment.trials
        category_rows.append(
            CategoryAnalysis(
                category=category,
                expected_executions=category_expected,
                terminal_executions=sum(
                    evidence.execution.status in TERMINAL_EXECUTION_STATUSES
                    for evidence in evidences
                ),
                graded_executions=category_graded,
                passing_executions=category_passing,
                strict_pass_rate=_ratio(category_passing, category_expected),
                grading_coverage=_ratio(category_graded, category_expected),
            )
        )

    expected = len(expected_coordinates)
    terminal = sum(status_counts[status] for status in TERMINAL_EXECUTION_STATUSES)
    completed = status_counts["completed"]
    infrastructure_failed = sum(status_counts[status] for status in {"failed", "enqueue_failed"})
    is_terminal = grid_integrity.valid and terminal == expected
    is_fully_graded = grid_integrity.valid and completed == expected and graded == expected
    stable_cases = stability_counter["stable_pass"] + stability_counter["stable_fail"]
    retry_attempts = sum(
        max(0, evidence.execution.attempt_count - 1) for evidence in expected_evidence
    )
    provider_requests = [
        turn for turn in rows.provider_turns if turn.request_started_at is not None
    ]
    provider_responses = [turn for turn in rows.provider_turns if turn.response_payload is not None]
    complete_token_usage = bool(provider_responses) and all(
        turn.input_tokens is not None
        and turn.output_tokens is not None
        and turn.total_tokens is not None
        for turn in provider_responses
    )
    token_usage_available = complete_token_usage and len(provider_requests) == len(
        provider_responses
    )
    complete_cost = bool(provider_responses) and all(
        turn.estimated_cost_usd_micros is not None for turn in provider_responses
    )
    cost_available = complete_cost and len(provider_requests) == len(provider_responses)
    total_cost_micros = sum(turn.estimated_cost_usd_micros or 0 for turn in provider_responses)
    provider_usage = None
    if experiment.provider == "openai-responses":
        provider_usage = ProviderUsage(
            request_count=len(provider_requests),
            response_count=len(provider_responses),
            input_tokens=sum(turn.input_tokens or 0 for turn in provider_responses),
            cached_input_tokens=sum(turn.cached_input_tokens or 0 for turn in provider_responses),
            output_tokens=sum(turn.output_tokens or 0 for turn in provider_responses),
            reasoning_tokens=sum(turn.reasoning_tokens or 0 for turn in provider_responses),
            total_tokens=sum(turn.total_tokens or 0 for turn in provider_responses),
            estimated_cost_usd=(
                f"{Decimal(total_cost_micros) / Decimal(1_000_000):.6f}" if cost_available else None
            ),
            returned_models=tuple(
                sorted(
                    {
                        turn.response_model
                        for turn in provider_responses
                        if turn.response_model is not None
                    }
                )
            ),
            latency=_duration_metric(
                [turn.latency_ms for turn in provider_requests if turn.latency_ms is not None]
            ),
        )

    return ExperimentAnalysis(
        experiment_id=experiment.id,
        status=experiment.status,
        suite_id=suite.id,
        suite_name=suite.name,
        suite_version=suite.version,
        suite_definition_hash=suite.definition_hash,
        prompt=(
            None
            if prompt is None
            else PromptIdentity(
                name=prompt.name,
                version=prompt.version,
                definition_hash=prompt.definition_hash,
            )
        ),
        prompt_is_published=None if prompt is None else prompt.is_published,
        provider=experiment.provider,
        provider_model=experiment.provider_model,
        provider_config_hash=experiment.provider_config_hash,
        trials=experiment.trials,
        max_attempts=experiment.max_attempts,
        job_timeout_seconds=experiment.job_timeout_seconds,
        max_tool_steps=experiment.max_tool_steps,
        retry_backoff_seconds=experiment.retry_backoff_seconds,
        created_at=experiment.created_at,
        completed_at=experiment.completed_at,
        result_label=(
            "synthetic infrastructure validation"
            if experiment.provider == "deterministic-fake"
            else "real-provider evaluation on synthetic ParcelDesk cases"
        ),
        is_terminal=is_terminal,
        is_fully_graded=is_fully_graded,
        quality_comparison_ready=experiment.status == "completed" and is_fully_graded,
        token_usage_available=token_usage_available,
        cost_available=cost_available,
        provider_usage=provider_usage,
        fault_injection_present=any(
            evidence.fault_plan is not None for evidence in rows.executions
        ),
        grid_integrity=grid_integrity,
        execution_counts=ExecutionCounts(
            expected=expected,
            terminal=terminal,
            completed=completed,
            infrastructure_failed=infrastructure_failed,
            nonterminal=expected - terminal,
            graded=graded,
            passing=passing,
            criterion_failing=criterion_failing,
            ungraded_terminal=ungraded_terminal,
        ),
        status_counts=dict(sorted(status_counts.items())),
        strict_pass_rate=_ratio(passing, expected),
        graded_pass_rate=_ratio(passing, graded),
        grading_coverage=_ratio(graded, expected),
        criterion_pass_rate=_ratio(passed_criteria, total_criteria),
        total_attempts=total_attempts,
        retry_attempts=retry_attempts,
        stability_counts=StabilityCounts(
            stable_pass=stability_counter["stable_pass"],
            stable_fail=stability_counter["stable_fail"],
            unstable=stability_counter["unstable"],
            incomplete=stability_counter["incomplete"],
        ),
        stable_case_rate=_ratio(stable_cases, len(rows.expected_cases)),
        timing=TimingAnalysis(
            label=(
                "synthetic infrastructure timing, not model latency"
                if experiment.provider == "deterministic-fake"
                else "client-observed provider/API and worker timing, not server-only model latency"
            ),
            queue_delay=_duration_metric(queue_durations),
            run_duration=_duration_metric(run_durations),
            end_to_end_duration=_duration_metric(end_to_end_durations),
        ),
        grader_families=tuple(
            GraderFamilyAnalysis(
                grader_name=name,
                passed_criteria=counts[0],
                total_criteria=counts[1],
                pass_rate=_ratio(counts[0], counts[1]),
            )
            for name, counts in sorted(family_counts.items())
        ),
        criteria=tuple(
            CriterionAnalysis(
                grader_name=name,
                criterion=criterion,
                passed=counts[0],
                total=counts[1],
                pass_rate=_ratio(counts[0], counts[1]),
            )
            for (name, criterion), counts in sorted(criterion_counts.items())
        ),
        categories=tuple(category_rows),
        cases=tuple(case_analyses),
    )


def analyze_experiment(session: Session, experiment_id: UUID) -> ExperimentAnalysis:
    loaded = _load_experiment_rows(session, (experiment_id,))
    rows = loaded.get(experiment_id)
    if rows is None:
        raise ExperimentAnalysisNotFoundError(str(experiment_id))
    return _analyze_rows(rows)


def _float_delta(
    baseline: float | None,
    candidate: float | None,
    *,
    available: bool = True,
) -> ValueDelta:
    delta = None
    if available and baseline is not None and candidate is not None:
        delta = round(candidate - baseline, 6)
    return ValueDelta(baseline=baseline, candidate=candidate, delta=delta)


def _int_delta(baseline: int | None, candidate: int | None) -> ValueDelta:
    delta = None if baseline is None or candidate is None else candidate - baseline
    return ValueDelta(baseline=baseline, candidate=candidate, delta=delta)


def _assert_comparable(
    baseline: ExperimentAnalysis,
    candidate: ExperimentAnalysis,
) -> None:
    if baseline.experiment_id == candidate.experiment_id:
        raise ExperimentComparisonConflictError("Comparison requires two distinct experiments")
    if (
        baseline.prompt is None
        or candidate.prompt is None
        or baseline.prompt_is_published is not True
        or candidate.prompt_is_published is not True
    ):
        raise ExperimentComparisonConflictError(
            "Prompt comparison requires two published prompt identities"
        )
    if baseline.prompt.name != candidate.prompt.name:
        raise ExperimentComparisonConflictError(
            "Prompt comparison requires prompts from the same named family"
        )
    if (
        baseline.prompt.version == candidate.prompt.version
        or baseline.prompt.definition_hash == candidate.prompt.definition_hash
    ):
        raise ExperimentComparisonConflictError(
            "Prompt comparison requires distinct prompt versions and hashes"
        )
    if baseline.fault_injection_present or candidate.fault_injection_present:
        raise ExperimentComparisonConflictError(
            "Fault-injected experiments are not eligible for prompt comparison"
        )
    if not baseline.grid_integrity.valid or not candidate.grid_integrity.valid:
        raise ExperimentComparisonConflictError(
            "Experiments are not comparable because a case/trial grid is incomplete or invalid"
        )
    policy_fields = (
        "suite_id",
        "suite_definition_hash",
        "provider",
        "provider_model",
        "provider_config_hash",
        "trials",
        "max_attempts",
        "job_timeout_seconds",
        "max_tool_steps",
        "retry_backoff_seconds",
    )
    mismatches = [
        field for field in policy_fields if getattr(baseline, field) != getattr(candidate, field)
    ]
    if baseline.provider == candidate.provider == OPENAI_PROVIDER:
        baseline_models = (
            [] if baseline.provider_usage is None else baseline.provider_usage.returned_models
        )
        candidate_models = (
            [] if candidate.provider_usage is None else candidate.provider_usage.returned_models
        )
        if not baseline_models or baseline_models != candidate_models:
            mismatches.append("returned_provider_models")
    baseline_grid = {
        (case.case_key, trial.trial_number) for case in baseline.cases for trial in case.trials
    }
    candidate_grid = {
        (case.case_key, trial.trial_number) for case in candidate.cases for trial in case.trials
    }
    if baseline_grid != candidate_grid:
        mismatches.append("case_trial_membership")
    if mismatches:
        raise ExperimentComparisonConflictError(
            "Experiments are not comparable; mismatched " + ", ".join(mismatches)
        )


def compare_experiments(
    session: Session,
    baseline_experiment_id: UUID,
    candidate_experiment_id: UUID,
) -> ExperimentComparison:
    loaded = _load_experiment_rows(
        session,
        (baseline_experiment_id, candidate_experiment_id),
    )
    missing = [
        str(experiment_id)
        for experiment_id in (baseline_experiment_id, candidate_experiment_id)
        if experiment_id not in loaded
    ]
    if missing:
        raise ExperimentAnalysisNotFoundError(", ".join(missing))

    baseline = _analyze_rows(loaded[baseline_experiment_id])
    candidate = _analyze_rows(loaded[candidate_experiment_id])
    _assert_comparable(baseline, candidate)
    quality_ready = baseline.quality_comparison_ready and candidate.quality_comparison_ready

    baseline_families = {row.grader_name: row for row in baseline.grader_families}
    candidate_families = {row.grader_name: row for row in candidate.grader_families}
    family_comparisons: list[GraderFamilyComparison] = []
    for name in sorted(set(baseline_families) | set(candidate_families)):
        baseline_row = baseline_families.get(name)
        candidate_row = candidate_families.get(name)
        baseline_rate = baseline_row.pass_rate if baseline_row is not None else _ratio(0, 0)
        candidate_rate = candidate_row.pass_rate if candidate_row is not None else _ratio(0, 0)
        family_delta = None
        if (
            quality_ready
            and baseline_rate.denominator == candidate_rate.denominator
            and baseline_rate.value is not None
            and candidate_rate.value is not None
        ):
            family_delta = round(candidate_rate.value - baseline_rate.value, 6)
        family_comparisons.append(
            GraderFamilyComparison(
                grader_name=name,
                baseline_pass_rate=baseline_rate,
                candidate_pass_rate=candidate_rate,
                pass_rate_delta=family_delta,
            )
        )

    baseline_cases = {row.case_key: row for row in baseline.cases}
    candidate_cases = {row.case_key: row for row in candidate.cases}
    case_comparisons: list[CaseComparison] = []
    for key in sorted(baseline_cases):
        baseline_case = baseline_cases[key]
        candidate_case = candidate_cases[key]
        case_delta = None
        classification: ComparisonStatus = "provisional"
        if (
            quality_ready
            and baseline_case.strict_pass_rate.value is not None
            and candidate_case.strict_pass_rate.value is not None
        ):
            case_delta = round(
                candidate_case.strict_pass_rate.value - baseline_case.strict_pass_rate.value,
                6,
            )
            if case_delta > 0:
                classification = "improved"
            elif case_delta < 0:
                classification = "regressed"
            else:
                classification = "unchanged"
        case_comparisons.append(
            CaseComparison(
                case_key=key,
                category=baseline_case.category,
                baseline_pass_rate=baseline_case.strict_pass_rate,
                candidate_pass_rate=candidate_case.strict_pass_rate,
                pass_rate_delta=case_delta,
                classification=classification,
            )
        )

    comparable_denominators = (
        baseline.strict_pass_rate.denominator == candidate.strict_pass_rate.denominator
    )
    comparable_criteria = (
        baseline.criterion_pass_rate.denominator == candidate.criterion_pass_rate.denominator
    )
    comparable_cases = (
        baseline.stable_case_rate.denominator == candidate.stable_case_rate.denominator
    )
    complete_timing_samples = (
        baseline.is_terminal
        and candidate.is_terminal
        and baseline.timing.run_duration.observed_count == baseline.execution_counts.expected
        and candidate.timing.run_duration.observed_count == candidate.execution_counts.expected
        and baseline.timing.run_duration.observed_count
        == candidate.timing.run_duration.observed_count
    )
    complete_attempt_samples = baseline.is_terminal and candidate.is_terminal
    return ExperimentComparison(
        baseline=baseline,
        candidate=candidate,
        quality_comparison_ready=quality_ready,
        strict_pass_rate=_float_delta(
            baseline.strict_pass_rate.value,
            candidate.strict_pass_rate.value,
            available=quality_ready and comparable_denominators,
        ),
        criterion_pass_rate=_float_delta(
            baseline.criterion_pass_rate.value,
            candidate.criterion_pass_rate.value,
            available=quality_ready and comparable_criteria,
        ),
        stable_case_rate=_float_delta(
            baseline.stable_case_rate.value,
            candidate.stable_case_rate.value,
            available=quality_ready and comparable_cases,
        ),
        mean_run_duration_ms=_int_delta(
            (baseline.timing.run_duration.mean_ms if complete_timing_samples else None),
            (candidate.timing.run_duration.mean_ms if complete_timing_samples else None),
        ),
        total_attempts=_int_delta(
            baseline.total_attempts if complete_attempt_samples else None,
            candidate.total_attempts if complete_attempt_samples else None,
        ),
        grader_families=tuple(family_comparisons),
        cases=tuple(case_comparisons),
    )
