param([string]$OutputName = '映流.exe', [switch]$CreateShortcut)
$ErrorActionPreference = 'Stop'
$canvasRoot = $PSScriptRoot
if ([IO.Path]::GetFileName($OutputName) -ne $OutputName -or !($OutputName.EndsWith('.exe'))) { throw '输出名称必须是本目录的 exe 文件名。' }
$iconPath = Join-Path $canvasRoot 'app.ico'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (!(Test-Path -LiteralPath $compiler)) { throw '请先安装 Windows .NET Framework 4.8。' }
$assetDirectory = Join-Path $canvasRoot 'qa'
New-Item -ItemType Directory -Force -Path $assetDirectory | Out-Null
$assetBuilder = Join-Path $canvasRoot 'qa\BuildBrandAssets.exe'
& $compiler /nologo /target:exe /platform:x64 /codepage:65001 /reference:System.Drawing.dll "/out:$assetBuilder" (Join-Path $canvasRoot 'qa\BuildBrandAssets.cs') (Join-Path $canvasRoot 'CanvasBrand.cs')
if ($LASTEXITCODE -ne 0) { throw '软件图标编译失败' }
& $assetBuilder $canvasRoot
if ($LASTEXITCODE -ne 0) { throw '软件图标导出失败' }
$shellBin = Join-Path $canvasRoot 'data\desktop-shell-bin'
New-Item -ItemType Directory -Force -Path $shellBin | Out-Null
& (Join-Path $canvasRoot 'scripts\restore-webview.ps1')
$outputPath = Join-Path $canvasRoot $OutputName
$pendingPath = Join-Path $canvasRoot '映流.pending.exe'
& $compiler /nologo /target:winexe /platform:x64 /codepage:65001 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Runtime.Serialization.dll /reference:System.Management.dll /reference:System.Web.Extensions.dll "/reference:$shellBin\Microsoft.Web.WebView2.Core.dll" "/reference:$shellBin\Microsoft.Web.WebView2.WinForms.dll" "/win32manifest:$(Join-Path $canvasRoot 'app.manifest')" "/win32icon:$iconPath" "/out:$pendingPath" (Join-Path $canvasRoot 'AssemblyInfo.cs') (Join-Path $canvasRoot 'Launcher.cs') (Join-Path $canvasRoot 'CanvasWindow.cs') (Join-Path $canvasRoot 'CanvasBrand.cs') (Join-Path $canvasRoot 'StartupWindow.cs')
if ($LASTEXITCODE -ne 0) { throw '软件启动器编译失败' }
if (Test-Path -LiteralPath $outputPath) {
    $backupRoot = Join-Path $canvasRoot 'data\update-backups'
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    Move-Item -LiteralPath $outputPath -Destination (Join-Path $backupRoot ([IO.Path]::GetFileNameWithoutExtension($OutputName) + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.exe'))
}
Move-Item -LiteralPath $pendingPath -Destination $outputPath
if (!$CreateShortcut -or $OutputName -ne '映流.exe') { Write-Output "已构建软件：$outputPath"; return }
$desktopDirectory = [Environment]::GetFolderPath('DesktopDirectory')
$shell = New-Object -ComObject WScript.Shell
$newShortcutPath = Join-Path $desktopDirectory '映流.lnk'
$oldShortcutPath = Join-Path $desktopDirectory '创作画布.lnk'
if (Test-Path -LiteralPath $newShortcutPath) {
    $existingShortcut = $shell.CreateShortcut($newShortcutPath)
    if ($existingShortcut.TargetPath -ne $outputPath) { throw '桌面已有其他同名快捷方式，请保留它并手动创建映流入口。' }
} elseif (Test-Path -LiteralPath $oldShortcutPath) {
    $existingShortcut = $shell.CreateShortcut($oldShortcutPath)
    if ($existingShortcut.TargetPath -eq (Join-Path $canvasRoot '创作画布.exe')) {
        Move-Item -LiteralPath $oldShortcutPath -Destination $newShortcutPath
    }
}
$shortcut = $shell.CreateShortcut($newShortcutPath)
$shortcut.TargetPath = $outputPath
$shortcut.WorkingDirectory = $canvasRoot
$shortcut.Description = '映流 · 图片与视频的无限创作画布'
$shortcut.IconLocation = (Join-Path $canvasRoot 'web\brand\canvas-brand-v1.ico') + ',0'
$shortcut.Save()
Write-Output "已构建软件和桌面入口：$($shortcut.TargetPath)"
