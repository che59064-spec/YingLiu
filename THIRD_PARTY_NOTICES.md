# 第三方组件与发布范围

映流的源码包不包含 ComfyUI、模型权重、用户工作流、用户素材或 AI 服务密钥。

- Windows 桌面窗口使用 Microsoft WebView2 SDK，固定版本与下载校验见 `dependencies.json`。构建脚本从 [Microsoft 的 NuGet 包](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4258.31)恢复组件。便携包随组件保留 `data/desktop-shell-bin/WebView2-LICENSE.txt`，使用及再分发遵循该许可。
- Windows 用户需要独立安装 [Microsoft WebView2 Evergreen Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。SDK DLL 与 Runtime 不同；Runtime 不放入本发布包。官方部署说明见 [Microsoft Learn](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution)。
- Python、ComfyUI、自定义节点、FFmpeg 及模型权重由用户自行安装，各自遵循其许可证与使用条款。映流不重新许可这些外部项目。
- AI API 由用户选择并自行提供凭据，请遵循对应服务条款。是否支持图片输入取决于所选服务和模型。

映流自身源码使用 [MIT License](LICENSE)；上述第三方组件及模型仍遵循各自的许可。
