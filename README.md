# 简单助手 (SimpleAssistant)

一个 Windows 系统托盘语音助手：按下右 Alt 键开始录音，松开后自动识别语音、调用大语言模型处理，并将结果粘贴到当前光标位置。支持自动捕获当前选中文字作为上下文。

## 功能

- **全局热键**：右 Alt 键一键触发录音（无需切换窗口）
- **语音识别**：对接兼容 OpenAI Whisper API 的 ASR 服务
- **大模型处理**：支持 OpenAI / 兼容接口（Ollama、vLLM 等）及 Anthropic Claude
- **上下文感知**：自动捕获当前选中文字，作为对话背景传给模型
- **自动粘贴**：处理完成后直接粘贴到光标所在位置
- **系统托盘**：常驻后台，不占用任务栏

## 系统要求

- Windows 10 / 11（64 位）
- Python 3.9+

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python main.py
```

## 打包为独立 exe

在 Windows 命令提示符中执行：

```bat
build.bat
```

输出文件：`dist\SimpleAssistant\SimpleAssistant.exe`

## 配置

首次运行后，点击托盘图标 → **打开主界面** → **设置**，填入：

| 配置项 | 说明 |
|--------|------|
| ASR 地址 | Whisper 兼容接口的 URL，例如 `http://localhost:8000/v1` |
| ASR API Key | 接口密钥（本地服务可留空或填任意值） |
| LLM 提供商 | `openai`（含兼容接口）或 `anthropic` |
| LLM Base URL | 自定义接口地址（使用官方 API 可留空） |
| LLM API Key | 大模型服务的 API 密钥 |
| 模型名称 | 例如 `gpt-4o`、`qwen3`、`claude-sonnet-4-6` |

配置保存在 `~\.simple_assistant\config.json`，**请勿将此文件提交到版本控制**。

## 项目结构

```
SimpleAssistant/
├── main.py                     # 程序入口
├── requirements.txt
├── SimpleAssistant.spec        # PyInstaller 打包配置
├── build.bat                   # 一键打包脚本
└── src/
    ├── config/                 # 配置管理
    ├── core/
    │   ├── win32_hotkey.py     # Windows 全局键盘钩子
    │   ├── hotkey_listener.py  # 热键 + 文字捕获
    │   ├── clipboard_util.py   # 选中文字获取（多策略）
    │   ├── recorder.py         # 音频录制
    │   ├── asr_client.py       # 语音识别客户端
    │   ├── llm_client.py       # 大模型客户端
    │   └── processor.py        # 主处理流程
    ├── db/                     # SQLite 历史记录
    └── ui/                     # PyQt5 界面
```

## 依赖

- [PyQt5](https://pypi.org/project/PyQt5/) — GUI 框架
- [sounddevice](https://pypi.org/project/sounddevice/) / [soundfile](https://pypi.org/project/SoundFile/) — 音频录制
- [openai](https://pypi.org/project/openai/) — OpenAI / 兼容 API 客户端
- [anthropic](https://pypi.org/project/anthropic/) — Anthropic Claude 客户端
- [pynput](https://pypi.org/project/pynput/) — 键盘模拟（粘贴）
- [pyperclip](https://pypi.org/project/pyperclip/) — 剪贴板操作

## License

MIT
