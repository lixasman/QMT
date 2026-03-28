$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$codes = (Get-Content -Path "$root\backtest\default_universe_50.txt" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }) -join ","
$runTag = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = "$root\output\backtest_universe50_2025_k05_chip100_score035_cap_exitkB18_l2t07"
$logDir = Join-Path $outDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("backtest_bg_{0}.log" -f $runTag)

"START $(Get-Date -Format 's') run_tag=$runTag" | Out-File -FilePath $log -Encoding utf8
python -u -m backtest.main `
  --start 20250101 `
  --end 20251231 `
  --codes $codes `
  --initial-cash 300000 `
  --fee-bps 0.85 `
  --phase3-pathb-atr-mult 0.5 `
  --phase3-pathb-chip-min 1.0 `
  --phase2-score-threshold 0.35 `
  --out-dir "$outDir" `
  --light-logs `
  --exit-k-normal 2.0 `
  --exit-k-chip-decay 1.8 `
  --exit-layer2-threshold 0.7 `
  --allow-missing-chip `
  2>&1 | Tee-Object -FilePath $log -Append

"EXIT_CODE=$LASTEXITCODE $(Get-Date -Format 's')" | Out-File -FilePath $log -Append -Encoding utf8
