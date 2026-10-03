# mmdconv - batch PMX/PMD -> VRM 1.0 via headless Blender (mmd_tools + VRM Add-on)
#
#   convert.ps1 [files or folders ...] [-Preview] [-Options "--no-outline --author Me"]
#
# No arguments: converts everything in .\input (recursively).
# Output: .\output\<model name>\<model name>.vrm  (+ .blend for manual tweaks, .log.txt, optional previews)
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Paths,
    [switch]$Preview,
    [string]$Options = ""
)
$ErrorActionPreference = "Continue"   # Blender writes to stderr; "Stop" would abort on that
$root = $PSScriptRoot
. "$root\config.ps1"
if (-not (Test-Path $BlenderExe)) { throw "Blender not found at $BlenderExe - edit config.ps1" }

# allow "-Preview"/"-Options" to arrive through the .bat as plain args
$files = @()
$rest = @()
for ($i = 0; $i -lt $Paths.Count; $i++) {
    $p = $Paths[$i]
    if ($p -ieq "-Preview") { $Preview = $true; continue }
    if ($p -ieq "-Options") { $Options = $Paths[++$i]; continue }
    $rest += $p
}
if ($rest.Count -eq 0) { $rest = @("$root\input") }
foreach ($p in $rest) {
    if (Test-Path -LiteralPath $p -PathType Container) {
        $files += Get-ChildItem -LiteralPath $p -Recurse -File | Where-Object { $_.Extension -in ".pmx", ".pmd" } | ForEach-Object FullName
    } elseif (Test-Path -LiteralPath $p) {
        $files += (Resolve-Path -LiteralPath $p).Path
    } else {
        Write-Warning "not found: $p"
    }
}
if ($files.Count -eq 0) { Write-Host "Nothing to convert. Drop .pmx files on convert.bat or put them in $root\input"; exit 1 }

$ok = 0; $fail = @(); $used = @{}
foreach ($f in $files) {
    $name = [IO.Path]::GetFileNameWithoutExtension($f) -replace '[<>:"/\\|?*]', '_'
    if ($used.ContainsKey($name.ToLower())) {   # e.g. several packs all shipping "model.pmx"
        $name = (Split-Path (Split-Path $f -Parent) -Leaf) + "_" + $name
    }
    $used[$name.ToLower()] = $true
    $outDir = Join-Path $root "output\$name"
    New-Item -ItemType Directory -Force $outDir | Out-Null
    $vrm = Join-Path $outDir "$name.vrm"
    Write-Host ""
    Write-Host "==> $f" -ForegroundColor Cyan
    Remove-Item -LiteralPath $vrm -ErrorAction SilentlyContinue   # never report a stale file as success
    $a = @("-b", "--python-exit-code", "1", "--python", "$root\tools\pmx2vrm.py", "--", $f, $vrm) + ($Options -split ' ' | Where-Object { $_ })
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $BlenderExe @a 2>&1 | ForEach-Object {
        $l = "$_"
        if ($l -match '^\[pmx2vrm\]') { Write-Host ("   " + $l.Substring(10)) }
        elseif ($l -match 'Traceback|Error:') { Write-Host "   $l" -ForegroundColor Red }
    }
    $code = $LASTEXITCODE
    if ($code -eq 0 -and (Test-Path -LiteralPath $vrm)) {
        $ok++
        Write-Host ("   OK in {0:n0}s -> {1}" -f $sw.Elapsed.TotalSeconds, $vrm) -ForegroundColor Green
        if ($Preview) {
            & $BlenderExe -b --python "$root\tools\preview_vrm.py" -- $vrm (Join-Path $outDir "preview") 2>&1 | Out-Null
            Write-Host "   previews: $outDir\preview_*.png"
        }
    } else {
        $fail += $f
        Write-Host "   FAILED (exit $code) - see $outDir\$name.log.txt" -ForegroundColor Red
    }
}
Write-Host ""
Write-Host "Done: $ok converted, $($fail.Count) failed." -ForegroundColor ($(if ($fail.Count) { "Yellow" } else { "Green" }))
$fail | ForEach-Object { Write-Host "  failed: $_" -ForegroundColor Yellow }
