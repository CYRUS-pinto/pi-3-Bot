param([string]$targetIp = "10.70.4.81")
$env:SSH_ASKPASS = "C:\Users\Cyrus\.gemini\antigravity-ide\brain\e7ef5b92-5eb7-4dd5-90d2-6333f9ef795f\scratch\askpass.bat"
$env:SSH_ASKPASS_REQUIRE = "force"
$env:DISPLAY = "dummy:0"

$files = @("config.py", "vision.py", "main.py", "animation.py", "bridge.py", "hud.py", "arduino.py", "calibration.json", "calibrate_gestures.py")
Write-Host ">>> Deploying TARS updates to cyrus@${targetIp}:/home/cyrus/TARS/ ..." -ForegroundColor Cyan

foreach ($f in $files) {
    $localFile = Join-Path (Get-Location) $f
    if (Test-Path $localFile) {
        Write-Host "  -> Uploading $f ..." -NoNewline
        scp -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$localFile" "cyrus@${targetIp}:/home/cyrus/TARS/$f"
        if ($LASTEXITCODE -eq 0) {
            Write-Host " [DONE]" -ForegroundColor Green
        } else {
            Write-Host " [FAILED]" -ForegroundColor Red
        }
    }
}
Write-Host ">>> Deployment complete!" -ForegroundColor Green
