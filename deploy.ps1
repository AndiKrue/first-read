param(
    [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Deployment environment file not found: $EnvFile"
}

# Pass dotenv assignments directly to gcloud without writing their values.
$pairs = foreach ($line in Get-Content -LiteralPath $EnvFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
    if ($trimmed.StartsWith("export ")) { $trimmed = $trimmed.Substring(7).Trim() }
    if ($trimmed.Contains("=")) { $trimmed }
}
$envVars = "^~^" + ($pairs -join "~")

& gcloud run deploy first-read `
    --source . `
    --region us-central1 `
    --allow-unauthenticated `
    --memory 2Gi `
    --timeout 900 `
    --max-instances 3 `
    --concurrency 4 `
    --no-cpu-throttling `
    --set-env-vars $envVars
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
