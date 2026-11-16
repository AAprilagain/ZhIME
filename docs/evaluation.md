# 独立拼音评测复现

## 从已选聊天文本生成简拼 benchmark

准备 UTF-8 的聊天文本。TXT 每行一条消息，CSV 或 JSONL 提供 `text` 字段，也可用 `--text-field` 指定列名。只放入愿意用于实验的消息，提前处理人名和其他个人信息。

```powershell
python -m pip install jieba==0.42.1
python -m zhime.wechat_benchmark --input "E:\path\selected-chat.txt" --output benchmark-wechat --count 100
```

`items.txt` 按编号显示原句、分词、逐字全拼、无分隔符的全拼输入、最多五种简拼及词边界。`items.jsonl` 保存同批样本，供候选和模型评测使用。脚本取 5–16 字纯汉字短句，去重并按固定种子抽样。它会跳过含明显联系标记的消息，仍需人工检查人名、地点及多音字。简拼按词选择全拼或逐字首字母，变体少的句子会输出实际能生成的数量。按键串为合成输入，应按同一原句聚合全拼和简拼变体。

用 `--readings corrections.json` 校正多音字。JSON 的键为完整短句，值为逐字拼音数组，例如 `{"上午得去":["shang","wu","dei","qu"]}`。

## 冻结拼音评测

在本地运行目录执行数据与评测命令。依赖见 [requirements.txt](../requirements.txt)，Windows 工具链和雾凇词库导入见 [Windows 安装说明](windows.md)。本地的 `training-record.json` 记录固定模型和分词器的 SHA-256。Windows 候选生成使用小狼毫 0.17.4 中的 `rime.dll`，基准程序通过独立目录加载方案。

```powershell
$evalRoot = 'benchmark-repro'
$corpus = 'E:\path\to\export-r1'
python -m zhime.benchmark_v2 --root $corpus --train data/train.txt --old benchmark --out $evalRoot --seed 20261111
python -m zhime.rime_profiles --root "$evalRoot/rime-profile" --shared artifacts/lexicon/zhime_shared.dict.yaml
python -m zhime.freeze_v2 --root $evalRoot
python -m zhime.score_v2 --root $evalRoot --input "$evalRoot/frozen-dev.jsonl" --split dev --device cuda
python -m zhime.score_v2 --root $evalRoot --input "$evalRoot/frozen-test.jsonl" --split test --device cuda
```

评分也可使用 CPU。若在另一台机器评分，传输两个 `frozen-*.jsonl` 和冻结模型文件。开发评分会生成测试所需的 `selected.json`。使用新输出目录可以保留原始候选、缓存与结果。复现时核对样本、候选内容和指标，生成时延会随硬件、缓存和进程开销变化。

- `benchmark-v2/manifest.json` 与 `integrity.json` 保存分片来源、排除统计与校验值。
- `benchmark-v2/frozen-manifest.json` 与 `selected.json` 保存场景计数、搜索及评分参数。
- `benchmark-v2/test-results-v2.json` 保存总体、分组指标和配对区间，`test-predictions.jsonl` 保存逐例结果。
- `benchmark-v2/audit-summary.json` 记录 200 条定性抽查，`runtime/performance.json` 与 `native/acceptance.json` 记录 Windows 测量和验收。

## 10 亿汉字重训

在 Windows 从同一清洗语料重新抽取网页、评论、对话 80∶10∶10。整个源分片排除新基准开发集和测试集，以及旧评测的 `000_00009`。额外按简化后的文档哈希和完全相同的汉字短句筛除重合。该筛查无法保证排除跨分片的近重复。

```powershell
python -m zhime.data --root $corpus --output data/retrain-1b --han 1000000000 --seed 20261112 --exclude-shard 000_00009 --heldout benchmark-v2/items.jsonl --screen-workers 8
```

把该目录中的 `train.txt`、`manifest.json` 放入 Linux 运行目录的 `retrain-1b/`，保留 `artifacts/model.pt` 与 `artifacts/tokenizer.model` 作为参考，并确保已有 `benchmark-v2/` 的两份冻结候选文件和原测试结果。先查设备 UUID 和占用，绑定一张支持 BF16 的空闲 GPU。

```bash
CUDA_VISIBLE_DEVICES=<GPU_UUID> bash tools/retrain-1b-remote.sh
```

重训沿用原始 8,192 词表、分词器和 14,880,000 参数结构，从随机初始化开始。分词流式写盘，训练打乱无重叠的 128-token 目标窗口，遍历完整窗口一轮。采用 BF16 混合精度、批大小 64 和 AdamW，学习率先预热再余弦衰减。检查点每 500 步保存，正常中断后可用同一命令追加 `--resume`，从上一次已保存状态继续。训练和原部署保存在独立目录。

`retrain-1b/status` 记录阶段，`pipeline.log` 记录训练、分词与评测日志。管线先用开发集选择排序参数，再用原冻结候选池跑测试，并将新旧模型的配对 Top-1 差异和文档聚合置信区间写入 `retrain-1b/comparison.json`。

本次重训的本地复测产物保存在 `benchmark-v2/retrain-1b/`，包含开发集选择、测试结果、逐例预测和新旧配对比较。结果分析见[项目报告第 9 节](report.md)。

## 排除基准分片后的四卡重训

本次取清洗训练导出的全部允许分片，整块排除 `000_00009`、`000_00027`、`000_00045`、`001_00007` 和 `001_00025`。45 个允许分片在文档过滤前共约 230.19 亿汉字，实际入训规模以分词清单为准。沿用 10 亿版的原文筛查，另排除与外部 Standard development 原消息完全相同的文本。保留语料原来的来源分布。

入口为 `tools/retrain-23b-remote.sh`。在容器项目根目录的 `retrain-23b/` 放入 `inventory.json`、`parquet/`、`heldout.jsonl` 和 `external.jsonl`。其中 inventory 是以 `Name`、`Length` 标识允许 Parquet 文件的 JSON 数组，两个 JSONL 分别来自旧冻结基准和外部 Standard development。编码依赖 PyArrow、SentencePiece 和 `opencc-python-reimplemented==0.1.7`。如由宿主机向容器传输，用 `tools/stage-23b-remote.sh` 按清单逐个原子交付文件。

复用 8,192 词表和 14,880,000 参数结构，从随机初始化开始训练。四张 GPU 通过 UUID 绑定，每卡 batch 64，全局 batch 256。每轮打乱 64 步为单位的训练块及块内窗口，各 rank 使用互不重叠的窗口。训练一轮，仅舍弃分片末端无法形成完整窗口的 token 以及末尾不足一全局批次的窗口。每 1,000 步保存可恢复检查点。正式入口在编码完成后检查显卡占用，用 `torchrun` 启动四卡，随后在原冻结候选上复测并与 10 亿版本做配对比较。远程状态和日志保存在容器项目根目录的 `retrain-23b/status` 和 `retrain-23b/pipeline.log`。

### 约 200M 参数版本

本轮正式训练已改用 15 层、宽度 1,024、16 个注意力头的因果 Transformer，实测 197,465,088 个参数。保留 8,192 词表、128 token 上下文和共享输入输出嵌入，使用因果 SDPA、BF16 与每卡 batch 32，从随机初始化训练一轮。沿用已准备好的 token 分片，产物写入 `retrain-23b-200m/`。原目录 `retrain-23b/tokens/` 仍用于记录分词进度。

`tools/retrain-23b-200m-remote.sh` 等待 token 清单，核对四个 GPU UUID 和占用后启动训练。训练完成后在相同冻结池比较重排结果，并在 Standard IME development 子集上评估短联想。联想只给模型左侧上文，报告 2–8 字候选的精确 Top-1/Top-3，排除已知训练原消息重合。它衡量参考短语命中，人工可接受率及 Windows 响应延迟需要另行实测。
