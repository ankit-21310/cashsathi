$ErrorActionPreference = "Stop"
$env:APP_ENV = "development"
$env:GCP_PROJECT_ID = "cashsathi-local"
$env:FIREBASE_AUTH_EMULATOR_HOST = "127.0.0.1:9099"
$env:FIRESTORE_EMULATOR_HOST = "127.0.0.1:8080"
$env:CORS_ALLOWED_ORIGINS = "http://127.0.0.1:3000"
$env:WEB_BASE_URL = "http://127.0.0.1:3000"
$env:GMAIL_OAUTH_REDIRECT_URI = "http://127.0.0.1:8000/api/integrations/gmail/callback"
$env:NEXT_PUBLIC_PRODUCT_NAME = "Receivables Operator Preview"
$env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000"
$env:NEXT_PUBLIC_FIREBASE_API_KEY = "demo-key"
$env:NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN = "cashsathi-local.firebaseapp.com"
$env:NEXT_PUBLIC_FIREBASE_PROJECT_ID = "cashsathi-local"
$env:NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET = "cashsathi-local.firebasestorage.app"
$env:NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID = "000000000000"
$env:NEXT_PUBLIC_FIREBASE_APP_ID = "1:000000000000:web:local"
$env:NEXT_PUBLIC_USE_FIREBASE_EMULATOR = "true"

$api = Start-Process -FilePath "services/api/.venv/Scripts/uvicorn.exe" -ArgumentList @("cashsathi_api.main:app", "--host", "127.0.0.1", "--port", "8000") -PassThru -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP/cashsathi-api.log" -RedirectStandardError "$env:TEMP/cashsathi-api-error.log"

try {
  npm run seed
  if ($LASTEXITCODE -ne 0) { throw "Emulator seed failed" }
  npm run build:web
  if ($LASTEXITCODE -ne 0) { throw "Web build failed" }

  $web = Start-Process -FilePath "node" -ArgumentList @("apps/web/node_modules/next/dist/bin/next", "start", "apps/web", "--hostname", "127.0.0.1") -PassThru -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP/cashsathi-web.log" -RedirectStandardError "$env:TEMP/cashsathi-web-error.log"
  try {
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
      try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/healthz" | Out-Null; break } catch { Start-Sleep -Seconds 1 }
    }
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
      try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:3000" | Out-Null; break } catch { Start-Sleep -Seconds 1 }
    }
    $env:E2E_FIREBASE = "1"
    $env:E2E_BASE_URL = "http://127.0.0.1:3000"
    npm run test:e2e --workspace=web
    if ($LASTEXITCODE -ne 0) { throw "Playwright tests failed" }
  } finally {
    if ($web -and -not $web.HasExited) { Stop-Process -Id $web.Id -Force }
  }
} finally {
  if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
}
