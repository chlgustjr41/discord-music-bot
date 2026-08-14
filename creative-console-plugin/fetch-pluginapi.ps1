# Copies PluginApi.dll (+ xml/pdb) from the locally installed LogiPluginTool
# dotnet tool into ./sdk, for building without Logi Plugin Service installed.
# Mirrors streamdeck-plugin's fetch-ffmpeg pattern: fetched per-machine from a
# source the developer already installed, never committed to the repo.
$store = Join-Path $env:USERPROFILE ".dotnet\tools\.store\logiplugintool"
$dll = Get-ChildItem -Recurse -Path $store -Filter "PluginApi.dll" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $dll) {
  Write-Error "PluginApi.dll not found. Run: dotnet tool install --global LogiPluginTool"
  exit 1
}
$dest = Join-Path $PSScriptRoot "sdk"
New-Item -ItemType Directory -Force $dest | Out-Null
foreach ($ext in @("dll", "xml", "pdb")) {
  $src = [System.IO.Path]::ChangeExtension($dll.FullName, $ext)
  if (Test-Path $src) { Copy-Item $src $dest -Force }
}
Write-Host "PluginApi copied to $dest from $($dll.FullName)"
