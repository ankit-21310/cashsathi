param(
  [Parameter(Mandatory = $true)][string]$WebBaseUrl
)

$ErrorActionPreference = "Stop"

if (-not $env:JUDGE_EMAIL -or -not $env:JUDGE_PASSWORD) {
  throw "Set JUDGE_EMAIL and JUDGE_PASSWORD in the operator shell. Never commit them."
}
if (-not $WebBaseUrl.StartsWith("https://")) {
  throw "Production smoke testing requires an HTTPS WebBaseUrl."
}

$env:E2E_BASE_URL = $WebBaseUrl.TrimEnd("/")
npm run test:e2e --workspace=web -- production-smoke.spec.ts
