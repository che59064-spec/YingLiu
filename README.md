# 映流 YingLiu

把 ComfyUI 工作流放进中文无限画布，用卡片、素材和连线编排图片与视频创作。

支持多个独立画布、素材收纳、视频片段拼接、专业模式同步，以及可自行配置 AI API 的双语提示词优化。图片和视频生成仍由你自己的 ComfyUI 执行。

![映流启动界面预览](docs/assets/startup.png)

*启动界面预览；以下流程图为功能示意，不是实际画布截图。*

![从导入工作流到保存作品的流程示意图](docs/assets/workflow.svg)

## 主要功能

- 在中文无限画布中放置工作流、图片和视频，用连线组织参考素材。
- 创建、重命名、排序和切换多张画布，将作品放回画布并拼接视频片段。
- 导入普通 ComfyUI 工作流或 API 工作流；普通节点图可以在专业模式中继续编辑。
- 配置自己的 AI 服务优化提示词，查看中文对照；可选择发送图片与视频抽帧作为参考。
- 对适配的 MiniMax H3 工作流设置动作片段，并使用上一段末帧引导下一段开头。

## 开始使用

在 [Releases](../../releases) 下载 Windows 便携包，或下载源码自行构建。便携包不包含模型、ComfyUI 或 Python；首次使用前请准备以下依赖。

需要 Python 3.10 或以上版本。画布后台只使用 Python 标准库，无需额外安装 Python 包。生成图片、视频需要自行安装并启动 [ComfyUI](https://github.com/Comfy-Org/ComfyUI)，以及工作流所需节点和模型；视频拼接、视频参考抽帧还需要 FFmpeg 和 ffprobe 在 PATH 中。

Windows 原生窗口需要 64 位 Windows 10/11、.NET Framework 4.8、[WebView2 Evergreen Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。在软件目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

脚本会寻找已安装的 Python、保存仅本机使用的配置，并从 Microsoft NuGet 下载校验过的 WebView2 SDK 后构建 `映流.exe`。它不安装模型，不启动生成任务。需要桌面快捷方式时加 `-CreateShortcut`。Python 未被自动找到时，使用 `-Python "你的 Python 完整路径"`。

已有 Windows 便携包可直接打开 `映流.exe`；仍需本机 Python、WebView2 Runtime 和已有 ComfyUI。若不能自动发现 Python，运行 `setup.ps1 -SkipBuild`，或将 `config.example.json` 复制为 `config.json`，填写 `app_python`。便携包不包含 Python 和模型权重。

也可在 Windows、Linux 或 macOS 使用浏览器界面：

```shell
python server.py --port 8766
```

随后访问 `http://127.0.0.1:8766`。原生圆角、开屏和窗口按钮仅适用于 Windows；浏览器版本保留画布功能。ComfyUI 仍需运行在同一台机器。服务固定监听本机地址，不提供互联网多用户部署。

## 管理多张画布

画布左上角的标签可以拖动排序；右键标签或点击旁边的“画布管理”可重命名和删除。标签放不下时，使用两侧箭头翻看。至少保留一张画布，有生成或拼接任务的画布需要等任务结束后才能删除；删除前会保存本机备份，素材和作品保留。

## 接入自己的工作流

使用界面中的工作流导入入口选择 ComfyUI JSON。完整专业图保留在卡片内，常用参数在中文画布中显示；未适配的自定义节点可在专业模式继续编辑。

1. 配置并连接本机 ComfyUI，安装工作流依赖的自定义节点和模型。
2. 导入工作流 JSON，将工作流卡片添加到画布。
3. 把图片或动作视频拖到画布，并连接到工作流；选中卡片后调整参数。
4. 点击生成。生成的作品会回到画布，也可以在素材栏中查找并复用。

软件无法代替安装节点、下载模型或补齐原电脑上的输入文件。导入后请检查兼容性提示；缺失项需要在你的 ComfyUI 环境中准备好，工作流才能执行。不会自动执行导入文件或生成任务。

普通节点图与 API 工作流都可保存。普通节点图执行需要专业模式及同步桥把图转换为可执行请求；未知节点没有经过确认的中文字段时，继续在专业模式编辑。超过 JavaScript 安全整数范围的种子等数值保持原样，简化面板会只读显示，避免浏览器转换造成精度丢失。

专业模式双向同步需要把 `bridge/ComfyUI-CreationCanvas` 复制到 ComfyUI 的 `custom_nodes` 中，然后在无任务运行时由你重新启动 ComfyUI。配置本机目录后，软件自动启动 ComfyUI 时也会安装该桥；已在线的 ComfyUI 不会被强行重启。

## 动作片段接续

选中当前画布的 H3 动作工作流卡片，在卡片下方的“接续上一段”选择当前画布中已生成的视频。生成时提取所选视频最后一帧，作为下一段开头的画面引导；人物参考和动作片段仍按原工作流使用。该设置可随时清除，画面与音频的接缝仍需实际检查。

## 设置自己的 AI

打开 AI 设置，填写服务地址、模型名和密钥。保存只写入本机；点击连接测试或优化时才调用服务。支持 OpenAI 兼容接口、Anthropic Messages、Ollama 本地模型与 Agnes。接口类型描述的是协议，模型名称由你按服务商说明填写。不同模型的结构化输出、图片理解能力不同；默认通用返回模式，如服务明确支持可选 JSON 或严格 JSON Schema。

提示词优化先展示结果和中文对照，点击采用后才替换原文。启用图片参考时，当前工作流连接的图片和视频抽帧会发给你选择的 AI 服务；不会上传整个素材库。纯文字模式不发送图片。API 密钥不进入画布导出和发布包。

OpenAI 兼容接口对明确的 o1/o3/o4 与 GPT-5/GPT-6 模型使用推理参数格式；自定义中转别名无法判断真实模型，需要服务端适配。推理令牌也计入输出上限，内容触顶时会保留原提示词，不自动增加额度或重复请求。连接测试可能只确认接口连通，界面会说明是否收到完整文字。

## 本机引擎配置

右上角连接设置可填写 ComfyUI 地址、目录、Python 环境及自动启动选项。只连接已运行的 ComfyUI 时无需填写其目录。自动启动需要填写包含 `main.py` 的目录及该 ComfyUI 环境的 Python 路径。

高级设置放在 `config.json`，页面保存的 `data/settings.json` 优先于该文件；环境变量 `YINGLIU_PYTHON`、`YINGLIU_COMFY_ROOT`、`YINGLIU_COMFY_PYTHON` 可覆盖对应 Python 或目录。相对路径基于映流目录。

`comfy_args` 是附加启动参数的字符串列表，默认不预设显存策略；`extra_model_paths` 为 ComfyUI 额外模型路径配置文件的列表。`input_dir`、`output_dir` 可指向已有目录。换电脑时请重新设置这些路径。

## 数据与备份

画布、上传素材、工作流、任务记录和凭据保存在本机 `data/`，请定期离线备份并限制该目录访问。导入映流画布会新建独立画布，原画布继续保留。画布 JSON 导出不包含素材文件、模型和用户在“AI 设置”填写的 API 配置，换电脑仍需迁移素材并准备生成环境。原工作流中自行填写的 API 节点密钥仍属于工作流原图；软件只遮蔽其友好控件，不会擅自删除，分享前请自行清除。关闭窗口不会停止 ComfyUI 已在运行的任务。

## 开发与发布

```shell
python -m unittest discover -s tests
python scripts/package_release.py
python scripts/package_release.py --portable
```

部分媒体测试需要 FFmpeg；桥接插件的缓存测试需要 ComfyUI 环境中的 PyTorch。源码包使用文件白名单，排除 `data/`、个人 `config.json`、日志、模型、素材、旧安装包和个人测试截图；便携包仅额外加入本次构建的主程序及有官方来源的 WebView2 组件。

映流自身源码采用 [MIT 许可证](LICENSE)。相关依赖及模型使用各自的许可证，详见 [第三方说明](THIRD_PARTY_NOTICES.md) 和 [发布说明](docs/发布说明.md)。
