$ErrorActionPreference = "Stop"
$apiPort = if ($env:CASH_SATHI_E2E_API_PORT) { $env:CASH_SATHI_E2E_API_PORT } else { "8000" }
$webPort = if ($env:CASH_SATHI_E2E_WEB_PORT) { $env:CASH_SATHI_E2E_WEB_PORT } else { "3000" }
$env:APP_ENV = "development"
$env:GCP_PROJECT_ID = "cashsathi-local"
$env:FIREBASE_AUTH_EMULATOR_HOST = "127.0.0.1:9099"
$env:FIRESTORE_EMULATOR_HOST = "127.0.0.1:8080"
$env:CORS_ALLOWED_ORIGINS = "http://127.0.0.1:$webPort"
$env:WEB_BASE_URL = "http://127.0.0.1:$webPort"
$env:GMAIL_OAUTH_REDIRECT_URI = "http://127.0.0.1:$apiPort/api/integrations/gmail/callback"
$env:PLATFORM_ADMIN_UIDS = "demo-owner-a"
$env:NEXT_PUBLIC_PRODUCT_NAME = "Receivables Operator Preview"
$env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:$apiPort"
$env:NEXT_PUBLIC_FIREBASE_API_KEY = "demo-key"
$env:NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN = "cashsathi-local.firebaseapp.com"
$env:NEXT_PUBLIC_FIREBASE_PROJECT_ID = "cashsathi-local"
$env:NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET = "cashsathi-local.firebasestorage.app"
$env:NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID = "000000000000"
$env:NEXT_PUBLIC_FIREBASE_APP_ID = "1:000000000000:web:local"
$env:NEXT_PUBLIC_USE_FIREBASE_EMULATOR = "true"
$env:NEXT_DIST_DIR = ".next-e2e"

$api = Start-Process -FilePath "services/api/.venv/Scripts/uvicorn.exe" -ArgumentList @("cashsathi_api.main:app", "--host", "127.0.0.1", "--port", $apiPort) -PassThru -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP/cashsathi-api.log" -RedirectStandardError "$env:TEMP/cashsathi-api-error.log"

try {
  Push-Location "services/api"
  try {
    & ".venv/Scripts/python.exe" -m pytest -m firestore
  } finally {
    Pop-Location
  }
  if ($LASTEXITCODE -ne 0) { throw "Firestore repository integration tests failed" }
  npm run seed
  if ($LASTEXITCODE -ne 0) { throw "Emulator seed failed" }
  npm run build:web
  if ($LASTEXITCODE -ne 0) { throw "Web build failed" }

  $web = Start-Process -FilePath "node" -ArgumentList @("apps/web/node_modules/next/dist/bin/next", "start", "apps/web", "--hostname", "127.0.0.1", "--port", $webPort) -PassThru -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP/cashsathi-web.log" -RedirectStandardError "$env:TEMP/cashsathi-web-error.log"
  try {
    $apiReady = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
      try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$apiPort/readyz" | Out-Null; $apiReady = $true; break } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $apiReady) {
      Get-Content "$env:TEMP/cashsathi-api.log" -ErrorAction SilentlyContinue
      Get-Content "$env:TEMP/cashsathi-api-error.log" -ErrorAction SilentlyContinue
      throw "API did not become ready"
    }
    $webReady = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
      try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$webPort" | Out-Null; $webReady = $true; break } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $webReady) {
      Get-Content "$env:TEMP/cashsathi-web.log" -ErrorAction SilentlyContinue
      Get-Content "$env:TEMP/cashsathi-web-error.log" -ErrorAction SilentlyContinue
      throw "Web app did not become ready"
    }
    $env:E2E_FIREBASE = "1"
    $env:E2E_BASE_URL = "http://127.0.0.1:$webPort"
    npm run test:e2e --workspace=web
    if ($LASTEXITCODE -ne 0) {
      Get-Content "$env:TEMP/cashsathi-api.log" -ErrorAction SilentlyContinue
      Get-Content "$env:TEMP/cashsathi-api-error.log" -ErrorAction SilentlyContinue
      Get-Content "$env:TEMP/cashsathi-web.log" -ErrorAction SilentlyContinue
      Get-Content "$env:TEMP/cashsathi-web-error.log" -ErrorAction SilentlyContinue
      throw "Playwright tests failed"
    }
  } finally {
    if ($web -and -not $web.HasExited) { Stop-Process -Id $web.Id -Force }
  }
} finally {
  if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
}
