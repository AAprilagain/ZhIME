# ZhIME：自研 Windows 全拼输入法与候选重排实验

当前 Windows 版入口见 [项目主页](../README.md)。下文记录早期 14.88M 模型及小狼毫实验。

## 首版实验记录

以下命令从本地运行目录执行。首版权重 `artifacts/model.pt` 和 10 亿汉字重训权重 `artifacts/1b/model.pt` 均保留用于对比。首版结果见 [原报告](initial-report.md)。

### 数据与训练

```powershell
python -m pip install -r requirements.txt
python -m zhime.data --root "E:\path\to\export-r1" --output data --han 100000000 --seed 20261110
python -m zhime.train --data data/train.txt --out artifacts --steps 20000 --batch 32 --save-every 1000
```

`data/manifest.json` 记录样本量、抽中的文件和输出 SHA-256。网页和评论样本取自随机排列的分片及行组，因此是确定性的分层抽样，并非全数据集逐文档等概率抽样。务必使用独立验证数据，不能用训练集调参。模型参数和损失写入标准输出；`artifacts/model.pt` 每 1000 步覆盖。训练脚本目前不支持断点续训或自动验证集选模。

### 小狼毫接入实验

1. 安装包含 `librime-lua` 的 [小狼毫](https://github.com/rime/weasel)，启用朙月拼音。可选安装与小狼毫所用 Lua 版本和位数匹配的 LuaSocket；未安装时使用本地临时文件通道。
2. 在本项目目录运行 `powershell -ExecutionPolicy Bypass -File .\install-rime.ps1`；如果已有同名配置，按提示手动合并以免覆盖。之后通过小狼毫菜单重新部署。脚本会在 Rime 用户目录写入 `zhime-spool-path.txt`，指向本项目的 `spool` 目录。
3. 在项目目录创建 Python 虚拟环境，安装 `torch`、`sentencepiece`；放入已训练的 `artifacts/model.pt` 和 `artifacts/tokenizer.model`，启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-server.ps1
```

启动脚本会在后台运行服务并检查状态，可以关闭执行命令的窗口；重复执行时会提示服务已经运行。若状态显示「服务标记过期」，重新运行此脚本；启动失败时检查 `spool/service.stderr.log`。

服务仅监听 `127.0.0.1:18765`；没有 LuaSocket 时，请求以短暂文件形式存放在本项目 `spool` 目录，包含十六进制编码的近期输入内容，处理后删除；异常退出可能留下文件，下次启动会清理过期请求，可手动清空该目录。默认最多重排 20 项，最多保留 128 个近期提交字符；服务缺失、超时或回复错误时原序输出。长上下文的计算可能超过 150 ms 超时而回退。`Ctrl+Shift+Backspace` 请求清空当前输入法会话中的上下文，切换方案或重启输入法也会释放该会话上下文；不保证切换应用自动清空。实际快捷键和会话生命周期需在所用小狼毫版本验证。关闭服务即停用重排；删除添加的 Lua、配置项和 `zhime-spool-path.txt` 及临时目录并重新部署即可卸载扩展。

在记事本等文本框按 `Win+空格` 切换到「小狼毫」，再按 `F4` 选择「朙月拼音」或其简化字方案，确认处于中文模式后输入 `nihao`。正常应出现候选窗口；当模型成功返回时，首个候选的注释会显示 `[模型]`。此标记表示本次候选已评分，排序不一定改变。若只有原始候选而无标记，可能是服务未启动、超时或使用了未安装扩展的其他方案；如果连候选窗口都没有，先检查 Windows 输入法和中文模式。服务已运行时重复执行启动脚本会提示已有实例。

当前机器的 `.venv` 使用 CPU 版 PyTorch，并通过 `.pth` 引用该机器已有的 Anaconda 和用户 Python 包；它不是可搬迁的交付物。更换机器时应新建虚拟环境并独立安装依赖。

### 首版基准

`tools/rime_candidates.cpp` 用独立的 Rime 用户目录生成候选，禁用个人词频干扰。`zhime.make_benchmark` 从原验证集按文档键划分开发/测试；对话测试使用原对话测试文件。运行前需安装 librime 开发包、朙月拼音词典和 `pypinyin`，在隔离 Rime 数据目录部署方案。记录 Rime 和词典版本，保证开发和测试使用同一版本与配置。

```bash
g++ tools/rime_candidates.cpp -lrime -o rime_candidates
python -m zhime.make_benchmark --root /path/to/export-r1 --output benchmark --per-source 70 --rime-command ./rime_candidates /path/to/rime-data /path/to/clean-rime-profile
python -m zhime.evaluate --input benchmark/dev.jsonl --checkpoint artifacts/model.pt --tokenizer artifacts/tokenizer.model --output dev-results.json --tune
# 将开发集选出的 alpha 和 length_exponent 传给测试命令；不要在测试集上调参。
python -m zhime.evaluate --input benchmark/test.jsonl --checkpoint artifacts/model.pt --tokenizer artifacts/tokenizer.model --output test-results.json --alpha 0.5 --length-exponent 0 --preserve-length
```

以上为本次开发集选定参数；重新训练时必须重新在开发集选定。评估输出包含样本数、候选召回、全流程准确率、候选存在时的条件准确率、Top-5、配对精确检验和逐例预测。数据来源许可、划分独立性及合成拼音的多音字误差需另行核实。

## 来源

- VimeML：<https://github.com/Voltline/VimeML>，源代码及公开模型许可见上游仓库。
- Rime 和小狼毫：<https://github.com/rime/librime>、<https://github.com/rime/weasel>；朙月拼音词典及依赖分别遵循各自许可。
- 本地数据：`zh-v13-export-r1`；当前导出清单未提供各底层数据集完整许可，不能据此宣称可公开再分发。
