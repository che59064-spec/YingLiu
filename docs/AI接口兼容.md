# 提示词 AI 接口

这是提示词优化连接，图片、视频生成继续走用户自己的 ComfyUI。保存设置不会发起外部请求；“保存并测试”只发一句固定短文本，不带画布或参考素材，不自动重试。

| 接口类型 | 请求方式 | 适用说明 |
| --- | --- | --- |
| OpenAI 兼容 | Chat Completions | 可填写服务商基础地址、完整 chat/completions 地址及自己的模型名 |
| Anthropic | Messages | 独立 x-api-key 与版本请求头；图片转为 Messages 数据块 |
| Ollama | 原生 /api/chat | 本机模型可免密钥；需自行拉取模型 |
| Agnes | Chat Completions | 保留已有 H3、千问专用规范与参考抽帧 |

默认通过提示词要求 JSON，避免依赖服务商可选的 response_format。JSON 与严格 Schema 仅对明确支持的服务开启。看图能力是用户显式开关，纯文字模式不会读取或上传参考画面。视频仅使用选中范围的 12 帧，不发送原始视频或音频。未知工作流使用通用改写，不冒用 H3 专有语法。

Windows 的连接密钥使用当前用户 DPAPI 保护，其他系统使用仅所有者可读文件。状态接口不回传密钥；换服务或地址不会沿用旧密钥。配置不属于画布导出，不进入发布包；自行写在原始 ComfyUI 节点里的密钥仍需分享前清除。

OpenAI 推理模型识别仅限明确的 o1/o3/o4、GPT-5/GPT-6 名称。转发服务的自定义别名不能被自动推断；接口测试额度耗尽但有有效响应时，只提示连通，不宣称完整文字输出已验证。没有做各家付费服务的穷举实测。

协议依据：[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[OpenAI 模型参数兼容](https://developers.openai.com/api/docs/guides/latest-model)、[Anthropic Messages](https://platform.claude.com/docs/en/build-with-claude/working-with-messages)、[Ollama Chat](https://docs.ollama.com/api/chat)。工作流规范依据：[ComfyUI Workflow JSON](https://github.com/Comfy-Org/docs/blob/main/specs/workflow_json.mdx)、[控件序列化约定](https://github.com/Comfy-Org/ComfyUI_frontend/blob/main/docs/WIDGET_SERIALIZATION.md)。
