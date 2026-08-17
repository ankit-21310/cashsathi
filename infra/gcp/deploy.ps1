param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [Parameter(Mandatory = $true)][string]$FirebaseApiKey,
  [Parameter(Mandatory = $true)][string]$FirebaseAuthDomain,
  [Parameter(Mandatory = $true)][string]$FirebaseStorageBucket,
  [Parameter(Mandatory = $true)][string]$FirebaseMessagingSenderId,
  [Parameter(Mandatory = $true)][string]$FirebaseAppId,
  [string]$PlatformAdminUids = "",
  [string]$Region = "asia-south1"
)

$ErrorActionPreference = "Stop"
if (-not $PlatformAdminUids.Trim()) {
  throw "Strict production readiness requires at least one platform administrator UID."
}
$tag = Get-Date -Format "yyyyMMdd-HHmmss"
$repository = "$Region-docker.pkg.dev/$ProjectId/cashsathi"
$escapedAdminUids = $PlatformAdminUids.Replace(",", "\,")

gcloud config set project $ProjectId
gcloud builds submit --config infra/cloudbuild/api.yaml --substitutions "_REGION=$Region,_REPOSITORY=cashsathi,_IMAGE_TAG=$tag" .
gcloud run deploy cashsathi-api --image "$repository/cashsathi-api:$tag" --region $Region --allow-unauthenticated --service-account "cashsathi-api@$ProjectId.iam.gserviceaccount.com" --min 0 --max 3 --cpu 1 --memory 1Gi --concurrency 8 --timeout 600 --set-env-vars "APP_ENV=production,RUNTIME_PLATFORM=gcp,STRICT_PRODUCTION_READINESS=true,GCP_PROJECT_ID=$ProjectId,FIRESTORE_DATABASE_ID=(default),CORS_ALLOWED_ORIGINS=https://bootstrap.invalid,WEB_BASE_URL=https://bootstrap.invalid,LOG_LEVEL=INFO,GEMINI_MODEL=gemini-3.6-flash,MAX_PDF_BYTES=10485760,MAX_PDF_PAGES=25,MAX_JSON_BYTES=262144,DEFAULT_REQUEST_TIMEOUT_SECONDS=30,AI_REQUEST_TIMEOUT_SECONDS=60,SCHEDULER_REQUEST_TIMEOUT_SECONDS=540,EXPORT_RECORD_LIMIT=10000,GEMINI_TIMEOUT_SECONDS=45,GMAIL_OAUTH_REDIRECT_URI=https://bootstrap.invalid/api/integrations/gmail/callback,GMAIL_KMS_KEY_NAME=projects/$ProjectId/locations/$Region/keyRings/cashsathi/cryptoKeys/gmail-oauth-tokens,SCHEDULER_SERVICE_ACCOUNT_EMAIL=cashsathi-scheduler@$ProjectId.iam.gserviceaccount.com,SCHEDULER_AUDIENCE=https://bootstrap.invalid,PLATFORM_ADMIN_UIDS=$escapedAdminUids" --set-secrets "GEMINI_API_KEY=gemini-api-key:latest,GMAIL_OAUTH_CLIENT_ID=gmail-oauth-client-id:latest,GMAIL_OAUTH_CLIENT_SECRET=gmail-oauth-client-secret:latest"
$apiUrl = gcloud run services describe cashsathi-api --region $Region --format "value(status.url)"

$webSubstitutions = "_REGION=$Region,_REPOSITORY=cashsathi,_IMAGE_TAG=$tag,_API_BASE_URL=$apiUrl,_FIREBASE_API_KEY=$FirebaseApiKey,_FIREBASE_AUTH_DOMAIN=$FirebaseAuthDomain,_FIREBASE_PROJECT_ID=$ProjectId,_FIREBASE_STORAGE_BUCKET=$FirebaseStorageBucket,_FIREBASE_MESSAGING_SENDER_ID=$FirebaseMessagingSenderId,_FIREBASE_APP_ID=$FirebaseAppId"
gcloud builds submit --config infra/cloudbuild/web.yaml --substitutions $webSubstitutions .
gcloud run deploy cashsathi-web --image "$repository/cashsathi-web:$tag" --region $Region --allow-unauthenticated --service-account "cashsathi-web@$ProjectId.iam.gserviceaccount.com" --min 0 --max 3 --cpu 1 --memory 512Mi --concurrency 80 --timeout 60
$webUrl = gcloud run services describe cashsathi-web --region $Region --format "value(status.url)"

gcloud run services update cashsathi-api --region $Region --update-env-vars "CORS_ALLOWED_ORIGINS=$webUrl,WEB_BASE_URL=$webUrl,GMAIL_OAUTH_REDIRECT_URI=$apiUrl/api/integrations/gmail/callback,SCHEDULER_AUDIENCE=$apiUrl"

$schedulerAccount = "cashsathi-scheduler@$ProjectId.iam.gserviceaccount.com"
gcloud run services add-iam-policy-binding cashsathi-api --region $Region --member "serviceAccount:$schedulerAccount" --role roles/run.invoker
$job = gcloud scheduler jobs describe cashsathi-hourly-recheck --location $Region --format "value(name)" 2>$null
if ($job) {
  gcloud scheduler jobs update http cashsathi-hourly-recheck --location $Region --uri "$apiUrl/api/jobs/recheck" --http-method POST --oidc-service-account-email $schedulerAccount --oidc-token-audience $apiUrl --schedule "0 * * * *" --time-zone "Asia/Kolkata" --attempt-deadline 600s
} else {
  gcloud scheduler jobs create http cashsathi-hourly-recheck --location $Region --uri "$apiUrl/api/jobs/recheck" --http-method POST --oidc-service-account-email $schedulerAccount --oidc-token-audience $apiUrl --schedule "0 * * * *" --time-zone "Asia/Kolkata" --attempt-deadline 600s
}

Write-Output "Web: $webUrl"
Write-Output "API: $apiUrl"
Write-Output "Add the web hostname to Firebase Authentication and the API callback URI to the Gmail OAuth client, then run the production smoke test."
