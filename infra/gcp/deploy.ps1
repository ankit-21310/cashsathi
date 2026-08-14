param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [Parameter(Mandatory = $true)][string]$FirebaseApiKey,
  [Parameter(Mandatory = $true)][string]$FirebaseAuthDomain,
  [Parameter(Mandatory = $true)][string]$FirebaseStorageBucket,
  [Parameter(Mandatory = $true)][string]$FirebaseMessagingSenderId,
  [Parameter(Mandatory = $true)][string]$FirebaseAppId,
  [string]$Region = "asia-south1"
)

$ErrorActionPreference = "Stop"
$tag = Get-Date -Format "yyyyMMdd-HHmmss"
$repository = "$Region-docker.pkg.dev/$ProjectId/cashsathi"

gcloud config set project $ProjectId
gcloud builds submit --config infra/cloudbuild/api.yaml --substitutions "_REGION=$Region,_REPOSITORY=cashsathi,_IMAGE_TAG=$tag" .
gcloud run deploy cashsathi-api --image "$repository/cashsathi-api:$tag" --region $Region --allow-unauthenticated --service-account "cashsathi-api@$ProjectId.iam.gserviceaccount.com" --min 0 --max 3 --cpu 1 --memory 1Gi --concurrency 8 --timeout 120 --set-env-vars "APP_ENV=production,GCP_PROJECT_ID=$ProjectId,FIRESTORE_DATABASE_ID=(default),CORS_ALLOWED_ORIGINS=https://bootstrap.invalid,LOG_LEVEL=INFO,GEMINI_MODEL=gemini-3.6-flash,MAX_PDF_BYTES=10485760,MAX_PDF_PAGES=25,GEMINI_TIMEOUT_SECONDS=45" --set-secrets "GEMINI_API_KEY=gemini-api-key:latest"
$apiUrl = gcloud run services describe cashsathi-api --region $Region --format "value(status.url)"

$webSubstitutions = "_REGION=$Region,_REPOSITORY=cashsathi,_IMAGE_TAG=$tag,_API_BASE_URL=$apiUrl,_FIREBASE_API_KEY=$FirebaseApiKey,_FIREBASE_AUTH_DOMAIN=$FirebaseAuthDomain,_FIREBASE_PROJECT_ID=$ProjectId,_FIREBASE_STORAGE_BUCKET=$FirebaseStorageBucket,_FIREBASE_MESSAGING_SENDER_ID=$FirebaseMessagingSenderId,_FIREBASE_APP_ID=$FirebaseAppId"
gcloud builds submit --config infra/cloudbuild/web.yaml --substitutions $webSubstitutions .
gcloud run deploy cashsathi-web --image "$repository/cashsathi-web:$tag" --region $Region --allow-unauthenticated --service-account "cashsathi-web@$ProjectId.iam.gserviceaccount.com" --min 0 --max 3 --cpu 1 --memory 512Mi --concurrency 80 --timeout 60
$webUrl = gcloud run services describe cashsathi-web --region $Region --format "value(status.url)"

gcloud run services update cashsathi-api --region $Region --update-env-vars "CORS_ALLOWED_ORIGINS=$webUrl"

Write-Output "Web: $webUrl"
Write-Output "API: $apiUrl"
Write-Output "Add the web hostname to Firebase Authentication > Settings > Authorized domains, then run the production smoke test."
