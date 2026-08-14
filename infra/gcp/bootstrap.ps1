param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [Parameter(Mandatory = $true)][string]$BillingAccountId,
  [string]$Region = "asia-south1",
  [string]$BudgetAmount = "10USD"
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
  throw "Install and authenticate the Google Cloud CLI before running this script."
}

gcloud config set project $ProjectId
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com firestore.googleapis.com firebase.googleapis.com secretmanager.googleapis.com cloudkms.googleapis.com cloudscheduler.googleapis.com gmail.googleapis.com billingbudgets.googleapis.com generativelanguage.googleapis.com logging.googleapis.com monitoring.googleapis.com

$repository = gcloud artifacts repositories describe cashsathi --location $Region --format "value(name)" 2>$null
if (-not $repository) {
  gcloud artifacts repositories create cashsathi --repository-format docker --location $Region --description "CashSathi service images"
}

foreach ($service in @("cashsathi-api", "cashsathi-web", "cashsathi-scheduler")) {
  $email = "$service@$ProjectId.iam.gserviceaccount.com"
  $existing = gcloud iam service-accounts describe $email --format "value(email)" 2>$null
  if (-not $existing) {
    gcloud iam service-accounts create $service --display-name $service
  }
}

$apiAccount = "cashsathi-api@$ProjectId.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$apiAccount" --role roles/datastore.user --condition None
gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$apiAccount" --role roles/logging.logWriter --condition None

$webAccount = "cashsathi-web@$ProjectId.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$webAccount" --role roles/logging.logWriter --condition None

foreach ($secret in @("gemini-api-key", "gmail-oauth-client-id", "gmail-oauth-client-secret")) {
  $existing = gcloud secrets describe $secret --format "value(name)" 2>$null
  if (-not $existing) {
    gcloud secrets create $secret --replication-policy automatic
  }
  gcloud secrets add-iam-policy-binding $secret --member "serviceAccount:$apiAccount" --role roles/secretmanager.secretAccessor
}

$keyRing = gcloud kms keyrings describe cashsathi --location $Region --format "value(name)" 2>$null
if (-not $keyRing) {
  gcloud kms keyrings create cashsathi --location $Region
}
$cryptoKey = gcloud kms keys describe gmail-oauth-tokens --keyring cashsathi --location $Region --format "value(name)" 2>$null
if (-not $cryptoKey) {
  gcloud kms keys create gmail-oauth-tokens --keyring cashsathi --location $Region --purpose encryption
}
gcloud kms keys add-iam-policy-binding gmail-oauth-tokens --keyring cashsathi --location $Region --member "serviceAccount:$apiAccount" --role roles/cloudkms.cryptoKeyEncrypterDecrypter

$database = gcloud firestore databases describe --database "(default)" --format "value(name)" 2>$null
if (-not $database) {
  gcloud firestore databases create --database "(default)" --location $Region --type firestore-native --edition standard --delete-protection
}

$budgetName = "CashSathi deadline guardrail"
$existingBudget = gcloud billing budgets list --billing-account $BillingAccountId --filter "displayName='$budgetName'" --format "value(name)"
if (-not $existingBudget) {
  gcloud billing budgets create --billing-account $BillingAccountId --display-name $budgetName --budget-amount $BudgetAmount --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0
}

npx firebase projects:addfirebase $ProjectId
npx firebase use $ProjectId
npx firebase deploy --only firestore:rules,firestore:indexes
gcloud firestore fields ttls update expires_at --collection-group "_rate_limits" --database "(default)" --enable-ttl --async

Write-Output "Bootstrap complete. Configure observability before smoke testing, then enable Google and Email/Password providers in Firebase Authentication."
