[CmdletBinding()]
param(
    [string]$BaseUrl = "http://localhost:8000",
    [ValidateRange(1, 30)]
    [int]$PollIntervalSeconds = 2,
    [ValidateRange(30, 3600)]
    [int]$TimeoutSeconds = 900,
    [ValidateLength(1, 200)]
    [ValidatePattern("^[A-Za-z0-9-]+$")]
    [string]$RunId = ([Guid]::NewGuid().ToString("N"))
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$apiBase = $BaseUrl.TrimEnd("/")

function Submit-PromptArm {
    param(
        [Parameter(Mandatory)]
        [string]$PromptVersion,
        [Parameter(Mandatory)]
        [string]$IdempotencyKey
    )

    $payload = @{
        suite_name = "parceldesk-refunds"
        suite_version = "2.0.0"
        provider = "deterministic-fake"
        prompt_name = "parceldesk-support-agent"
        prompt_version = $PromptVersion
        trials = 3
        max_attempts = 3
        job_timeout_seconds = 30
        max_tool_steps = 8
        retry_backoff_seconds = 1
    } | ConvertTo-Json -Compress

    $response = Invoke-WebRequest `
        -Uri "$apiBase/api/v1/experiments" `
        -Method Post `
        -UseBasicParsing `
        -ContentType "application/json" `
        -Headers @{ "Idempotency-Key" = $IdempotencyKey } `
        -Body $payload

    if ($response.StatusCode -ne 202) {
        throw "Expected experiment submission to return 202; received $($response.StatusCode)"
    }
    return $response.Content | ConvertFrom-Json
}

function Get-Experiment {
    param(
        [Parameter(Mandatory)]
        [string]$ExperimentId
    )

    return Invoke-RestMethod -Uri "$apiBase/api/v1/experiments/$ExperimentId"
}

$ready = Invoke-WebRequest -Uri "$apiBase/health/ready" -UseBasicParsing
if ($ready.StatusCode -ne 200) {
    throw "EvalGuard is not ready at $apiBase"
}

$suites = Invoke-RestMethod -Uri "$apiBase/api/v1/suites"
$prompts = Invoke-RestMethod -Uri "$apiBase/api/v1/prompts"
$v2Suite = $suites | Where-Object { $_.name -eq "parceldesk-refunds" -and $_.version -eq "2.0.0" }
$promptVersions = @(
    $prompts |
        Where-Object { $_.name -eq "parceldesk-support-agent" } |
        ForEach-Object { $_.version }
)
if ($null -eq $v2Suite -or "1.0.0" -notin $promptVersions -or "2.0.0" -notin $promptVersions) {
    throw "The v2 suite and both prompt versions must be seeded before running the demo"
}

$baseline = Submit-PromptArm `
    -PromptVersion "1.0.0" `
    -IdempotencyKey "portfolio-demo-baseline-$RunId"
$candidate = Submit-PromptArm `
    -PromptVersion "2.0.0" `
    -IdempotencyKey "portfolio-demo-candidate-$RunId"

$deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
do {
    $baselineDetail = Get-Experiment -ExperimentId $baseline.id
    $candidateDetail = Get-Experiment -ExperimentId $candidate.id

    if ($baselineDetail.status -eq "failed" -or $candidateDetail.status -eq "failed") {
        throw "A demo experiment failed; inspect its execution retrieval endpoints"
    }
    if ($baselineDetail.status -eq "completed" -and $candidateDetail.status -eq "completed") {
        break
    }
    if ([DateTimeOffset]::UtcNow -ge $deadline) {
        throw "Timed out waiting for both demo experiments to complete"
    }
    Start-Sleep -Seconds $PollIntervalSeconds
} while ($true)

$baselineSummary = Invoke-RestMethod `
    -Uri "$apiBase/api/v1/experiments/$($baseline.id)/summary"
$candidateSummary = Invoke-RestMethod `
    -Uri "$apiBase/api/v1/experiments/$($candidate.id)/summary"
$comparisonPath = "/api/v1/experiments/$($baseline.id)/compare/$($candidate.id)"
$comparison = Invoke-RestMethod -Uri "$apiBase$comparisonPath"
$reportUrl = "$apiBase/reports/experiments/$($baseline.id)/compare/$($candidate.id)"
$report = Invoke-WebRequest -Uri $reportUrl -UseBasicParsing
$rawLinkCount = ([regex]::Matches($report.Content, "/api/v1/executions/")).Count

if (-not $comparison.quality_comparison_ready -or $report.StatusCode -ne 200) {
    throw "The completed demo did not produce a ready comparison and report"
}
if (
    $baselineSummary.execution_counts.expected -ne 75 -or
    $candidateSummary.execution_counts.expected -ne 75 -or
    $baselineSummary.execution_counts.passing -ne 75 -or
    $candidateSummary.execution_counts.passing -ne 75 -or
    $baselineSummary.criterion_pass_rate.numerator -ne 1521 -or
    $candidateSummary.criterion_pass_rate.numerator -ne 1521 -or
    $baselineSummary.criterion_pass_rate.denominator -ne 1521 -or
    $candidateSummary.criterion_pass_rate.denominator -ne 1521 -or
    $rawLinkCount -ne 150
) {
    throw "The demo evidence did not match the immutable 150-execution reference path"
}

[pscustomobject]@{
    label = "synthetic infrastructure validation"
    baseline_experiment_id = $baseline.id
    candidate_experiment_id = $candidate.id
    baseline_executions = $baselineSummary.execution_counts.expected
    candidate_executions = $candidateSummary.execution_counts.expected
    baseline_passing = $baselineSummary.execution_counts.passing
    candidate_passing = $candidateSummary.execution_counts.passing
    baseline_criteria = $baselineSummary.criterion_pass_rate.denominator
    candidate_criteria = $candidateSummary.criterion_pass_rate.denominator
    comparison_ready = $comparison.quality_comparison_ready
    report_raw_links = $rawLinkCount
    report_url = $reportUrl
    limitation = "The deterministic fake ignores prompt semantics; this is not model-quality evidence."
} | ConvertTo-Json
