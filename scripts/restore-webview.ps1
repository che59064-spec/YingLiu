$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
$dependency = Get-Content -Raw -LiteralPath (Join-Path $appRoot 'dependencies.json') | ConvertFrom-Json
$cacheRoot = Join-Path $appRoot 'data\build-cache'
$shellRoot = Join-Path $appRoot 'data\desktop-shell-bin'
New-Item -ItemType Directory -Force -Path $cacheRoot,$shellRoot | Out-Null
$package = Join-Path $cacheRoot ('microsoft.web.webview2.' + $dependency.webview2.version + '.nupkg')
if (!(Test-Path -LiteralPath $package)) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $dependency.webview2.url -OutFile $package
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $package).Hash -ne $dependency.webview2.sha256) { throw 'WebView2 下载校验失败。请删除 data/build-cache 中对应下载文件后重试。' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($package)
try {
    $files = @{
        'lib/net462/Microsoft.Web.WebView2.Core.dll' = 'Microsoft.Web.WebView2.Core.dll'
        'lib/net462/Microsoft.Web.WebView2.WinForms.dll' = 'Microsoft.Web.WebView2.WinForms.dll'
        'runtimes/win-x64/native/WebView2Loader.dll' = 'WebView2Loader.dll'
        'LICENSE.txt' = 'WebView2-LICENSE.txt'
    }
    foreach ($entryName in $files.Keys) {
        $entry = $archive.GetEntry($entryName)
        if ($null -eq $entry) { throw "WebView2 包缺少组件：$entryName" }
        $destination = Join-Path $shellRoot $files[$entryName]
        $stream = $entry.Open()
        try { $memory = New-Object IO.MemoryStream; $stream.CopyTo($memory); $bytes = $memory.ToArray(); $memory.Dispose() } finally { $stream.Dispose() }
        if ((Test-Path -LiteralPath $destination) -and [Convert]::ToBase64String([IO.File]::ReadAllBytes($destination)) -eq [Convert]::ToBase64String($bytes)) { continue }
        [IO.File]::WriteAllBytes($destination, $bytes)
    }
} finally { $archive.Dispose() }
Write-Output ('桌面组件已校验：WebView2 ' + $dependency.webview2.version)
