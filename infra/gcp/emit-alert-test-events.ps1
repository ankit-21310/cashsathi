param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [switch]$ConfirmNotifications
)

$ErrorActionPreference = "Stop"
if (-not $ConfirmNotifications) {
  throw "Re-run with -ConfirmNotifications to emit synthetic events that intentionally trigger alerts."
}
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
  throw "Install and authenticate the Google Cloud CLI before emitting alert tests."
}

gcloud config set project $ProjectId
function Write-TestEvent {
  param([string]$Category, [string]$Sequence)
  $payload = @{
    category = $Category
    synthetic_alert_test = $true
    sequence = $Sequence
    message = "CashSathi controlled alert test; contains no customer data."
  } | ConvertTo-Json -Compress
  gcloud logging write cashsathi-alert-tests $payload --payload-type json --severity ERROR --resource cloud_run_revision --resource-labels "project_id=$ProjectId,service_name=cashsathi-alert-test,revision_name=synthetic,configuration_name=synthetic,location=global"
}

1..3 | ForEach-Object { Write-TestEvent "api_5xx" "api-$_" }
1..2 | ForEach-Object { Write-TestEvent "schema_or_model_failure" "model-$_" }
Write-TestEvent "gmail_failure" "gmail-1"
Write-TestEvent "scheduler_failure" "scheduler-1"

Write-Output "Synthetic events emitted. Confirm four alert notifications and document the test time."
Write-Output "The two-hour scheduler-absence policy must be verified separately during a controlled window."
