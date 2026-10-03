# Dev/QA helper: convert one PMX, render previews, dump structure. Output -> <OutDir>\qa.log
param([Parameter(Mandatory)][string]$Pmx, [Parameter(Mandatory)][string]$OutDir, [string]$Extra = "")
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
. "$root\config.ps1"
New-Item -ItemType Directory -Force $OutDir | Out-Null
$name = [IO.Path]::GetFileNameWithoutExtension($Pmx)
$vrm = Join-Path $OutDir "$name.vrm"
$log = Join-Path $OutDir "qa.log"
"=== convert $(Get-Date)" | Out-File $log -Encoding utf8
$a = @("-b", "--python", "$PSScriptRoot\pmx2vrm.py", "--", $Pmx, $vrm) + ($Extra -split ' ' | Where-Object { $_ })
& $BlenderExe @a 2>&1 | Select-String '\[pmx2vrm\]|Traceback|Error:' | ForEach-Object { $_.Line } | Out-File $log -Append -Encoding utf8
"=== preview" | Out-File $log -Append -Encoding utf8
& $BlenderExe -b --python "$PSScriptRoot\preview_vrm.py" -- $vrm (Join-Path $OutDir "qa") 2>&1 | Select-String 'PREVIEW_OK|Traceback|Error:' | ForEach-Object { $_.Line } | Out-File $log -Append -Encoding utf8
"=== inspect" | Out-File $log -Append -Encoding utf8
& $BlenderPython "$PSScriptRoot\vrm_inspect.py" $vrm 2>&1 | Out-File $log -Append -Encoding utf8
& $BlenderPython "$PSScriptRoot\vrm_bones.py" $vrm 2>&1 | Out-File $log -Append -Encoding utf8
"=== DONE" | Out-File $log -Append -Encoding utf8
