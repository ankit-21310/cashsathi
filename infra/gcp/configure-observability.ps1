param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [Parameter(Mandatory = $true)][string]$NotificationEmail
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
  throw "Install and authenticate the Google Cloud CLI before configuring observability."
}
gcloud config set project $ProjectId
gcloud services enable logging.googleapis.com monitoring.googleapis.com

function Ensure-LogMetric {
  param([string]$Name, [string]$Description, [string]$Filter)
  $existing = gcloud logging metrics describe $Name --format "value(name)" 2>$null
  if ($existing) {
    gcloud logging metrics update $Name --description $Description --log-filter $Filter
  } else {
    gcloud logging metrics create $Name --description $Description --log-filter $Filter
  }
}

Ensure-LogMetric "cashsathi_api_5xx" "CashSathi API 5xx responses" 'resource.type="cloud_run_revision" AND jsonPayload.category="api_5xx"'
Ensure-LogMetric "cashsathi_schema_model_failure" "Gemini schema or model failures" 'resource.type="cloud_run_revision" AND jsonPayload.category="schema_or_model_failure"'
Ensure-LogMetric "cashsathi_gmail_failure" "Gmail definite or ambiguous failures" 'resource.type="cloud_run_revision" AND jsonPayload.category="gmail_failure"'
Ensure-LogMetric "cashsathi_scheduler_failure" "CashSathi scheduler failures" 'resource.type="cloud_run_revision" AND jsonPayload.category="scheduler_failure"'
Ensure-LogMetric "cashsathi_scheduler_success" "CashSathi scheduler success heartbeat" 'resource.type="cloud_run_revision" AND jsonPayload.category="scheduler_success"'
Ensure-LogMetric "cashsathi_billing_failure" "CashSathi billing provider or verification failures" 'resource.type="cloud_run_revision" AND (jsonPayload.category="billing_provider_failure" OR jsonPayload.category="billing_verification_failure")'

$channel = gcloud beta monitoring channels list --filter "type=email AND labels.email_address=$NotificationEmail" --format "value(name)" --limit 1
if (-not $channel) {
  $channelFile = New-TemporaryFile
  try {
    @{
      type = "email"
      displayName = "CashSathi production alerts"
      labels = @{ email_address = $NotificationEmail }
      enabled = $true
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $channelFile.FullName -Encoding utf8
    $channel = gcloud beta monitoring channels create --channel-content-from-file $channelFile.FullName --format "value(name)"
  } finally {
    Remove-Item -LiteralPath $channelFile.FullName -Force -ErrorAction SilentlyContinue
  }
}

function Ensure-Policy {
  param([string]$DisplayName, [hashtable]$Condition)
  $existing = gcloud alpha monitoring policies list --filter "displayName='$DisplayName'" --format "value(name)" --limit 1
  if ($existing) {
    Write-Output "Alert policy already exists: $DisplayName"
    return
  }
  $policyFile = New-TemporaryFile
  try {
    @{
      displayName = $DisplayName
      combiner = "OR"
      enabled = $true
      notificationChannels = @($channel)
      alertStrategy = @{
        notificationRateLimit = @{ period = "900s" }
        autoClose = "86400s"
      }
      conditions = @($Condition)
      documentation = @{
        content = "Follow docs/operations/production-runbook.md and correlate by request ID."
        mimeType = "text/markdown"
      }
    } | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $policyFile.FullName -Encoding utf8
    gcloud alpha monitoring policies create --policy-from-file $policyFile.FullName
  } finally {
    Remove-Item -LiteralPath $policyFile.FullName -Force -ErrorAction SilentlyContinue
  }
}

function Metric-Condition {
  param([string]$Name, [string]$Metric, [double]$Threshold, [string]$Window)
  return @{
    displayName = $Name
    conditionThreshold = @{
      filter = ('metric.type="logging.googleapis.com/user/{0}" AND resource.type="cloud_run_revision"' -f $Metric)
      comparison = "COMPARISON_GT"
      thresholdValue = $Threshold
      duration = "0s"
      aggregations = @(@{
        alignmentPeriod = $Window
        perSeriesAligner = "ALIGN_DELTA"
        crossSeriesReducer = "REDUCE_SUM"
      })
      trigger = @{ count = 1 }
    }
  }
}

Ensure-Policy "CashSathi API 5xx spike" (Metric-Condition "3 or more API 5xx in 5 minutes" "cashsathi_api_5xx" 2 "300s")
Ensure-Policy "CashSathi Gemini failure spike" (Metric-Condition "2 or more schema/model failures in 15 minutes" "cashsathi_schema_model_failure" 1 "900s")
Ensure-Policy "CashSathi Gmail failure" (Metric-Condition "Any Gmail failure" "cashsathi_gmail_failure" 0 "300s")
Ensure-Policy "CashSathi scheduler failure" (Metric-Condition "Any scheduler failure" "cashsathi_scheduler_failure" 0 "300s")
Ensure-Policy "CashSathi billing failure" (Metric-Condition "Any billing failure" "cashsathi_billing_failure" 0 "300s")
Ensure-Policy "CashSathi scheduler heartbeat missing" @{
  displayName = "No scheduler success for two hours"
  conditionAbsent = @{
    filter = 'metric.type="logging.googleapis.com/user/cashsathi_scheduler_success" AND resource.type="cloud_run_revision"'
    duration = "7200s"
    aggregations = @(@{
      alignmentPeriod = "3600s"
      perSeriesAligner = "ALIGN_DELTA"
      crossSeriesReducer = "REDUCE_SUM"
    })
    trigger = @{ count = 1 }
  }
}

Write-Output "Observability configured. Log-based metrics begin counting only new log entries."
