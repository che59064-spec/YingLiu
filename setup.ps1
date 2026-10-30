param([string]$Python, [string]$ComfyRoot, [switch]$SkipBuild, [switch]$CreateShortcut)
$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
if (!$Python) {
    $localPython = Join-Path $appRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $localPython) { $Python = $localPython }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { $Python = & py -3 -c 'import sys;print(sys.executable)' }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python = & python -c 'import sys;print(sys.executable)' }
}
if (!$Python -or !(Test-Path -LiteralPath $Python)) { throw '请安装 Python 3.10 或以上版本，或使用 -Python 指定已有 Python 完整路径。' }
& $Python -c 'import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)'
if ($LASTEXITCODE -ne 0) { throw '映流需要 Python 3.10 或以上版本。' }
$configPath = Join-Path $appRoot 'config.json'
if (Test-Path -LiteralPath $configPath) { $config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json }
else { $config = Get-Content -Raw -LiteralPath (Join-Path $appRoot 'config.example.json') | ConvertFrom-Json }
$config | Add-Member -NotePropertyName app_python -NotePropertyValue $Python -Force
if ($ComfyRoot) {
    if (!(Test-Path -LiteralPath (Join-Path $ComfyRoot 'main.py'))) { throw 'ComfyRoot 需要指向包含 main.py 的 ComfyUI 文件夹。' }
    $config | Add-Member -NotePropertyName comfy_root -NotePropertyValue ([IO.Path]::GetFullPath($ComfyRoot)) -Force
}
$config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $configPath -Encoding UTF8
if (!$SkipBuild) { & (Join-Path $appRoot 'build.ps1') -CreateShortcut:$CreateShortcut }
Write-Output '本机配置已保存。完成后可打开 映流.exe；图片和视频仍需你已有的 ComfyUI、节点和模型。'
