# ZhIME

ZhIME 是一个 Windows x64 中文拼音输入法。自研拼音引擎基于雾凇词库生成候选，197.47M 参数的语言模型使用最近上屏文本做候选重排和连续联想。支持全拼、简拼、混合简拼和六组可切换的模糊音。

TSF 先显示词典候选，再异步应用模型重排。开始选词后候选顺序锁定。切到 ZhIME 时后台自动启动，切走后释放 GPU。本项目使用 Rime 作为评测基线。

## 源码结构

| 路径 | 内容 |
| --- | --- |
| `src/zhime/` | 拼音引擎、模型推理、训练与 benchmark 代码 |
| `native/` | Windows TSF 前端和注册辅助程序 |
| `configs/` | 部署与消融实验配置 |
| `tools/` | 评测、构建、安装和打包辅助脚本 |
| `docs/` | Windows 使用说明、评测复现和项目报告 |
| `tests/` | 引擎、分词、模型和服务的自动化测试 |

## 开发与运行

使用 Python 3.10 或更新版本。在独立环境中安装源码和测试依赖。

```powershell
python -m pip install -e .
python -m pytest
powershell -ExecutionPolicy Bypass -File .\build-native.ps1
```

构建 Windows 组件需要 CMake、Visual Studio 2019 的 C++ 工具链和 Windows SDK。源码仓库可运行自动化测试。完整输入法运行还需要词库、分词器、模型和对应的 Python 运行环境。这些本地产物使用与源码同级的 `zhime-local/` 目录，路径和准备步骤见 [Windows 安装与运行](docs/windows.md)。

- [模型说明](MODEL_CARD.md)
- [评测复现](docs/evaluation.md)
- [项目报告](docs/report.md)
- [许可证及第三方来源](THIRD_PARTY.md)
