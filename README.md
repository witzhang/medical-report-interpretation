# Medical Report Interpretation（体检报告解读）

把体检报告 PDF（1 份或多年多份）变成一份**给人看的完整解读报告**，并对报告做
逐条数据对账——数字由脚本从原始 PDF 里"取"，读不懂就停下报错，全程在本机跑。

- 入口：[`SKILL.md`](SKILL.md)（给 agent 用）· 上手：[`QUICKSTART.md`](QUICKSTART.md)
- 合成样例与最小 eval：[`examples/`](examples/) · `python3 scripts/eval_synth.py`

## 支持哪些输入（矩阵）

| 输入 | 支持 | 说明 |
|---|---|---|
| **文字层 PDF**（机构导出的电子报告） | ✅ 全承诺 | 每个数字可追回页码；双跑一致 |
| 疑似 OCR 叠层 PDF（扫描件套了文字层） | ⚠️ 降级（需显式 `--allow-degraded`） | 数字可能来自图像识别 —— 会标出来，不静默当正常件 |
| 扫描件 / 翻拍件（无文字层） | ❌ | v1 无 OCR：数字无法逐字核对，宁可停下也不猜；请索取电子版 PDF |
| 加密/受保护 PDF | ❌ | 解除保护后重试 |
| OFD / Word / Excel / CSV / 图片 | ❌ | 请提供 PDF 版报告（表格与 OFD 在路线图上） |

先判一遍再动手（"不支持"会带上**原因 + 下一步**，不是一句报错）：

```bash
python3 scripts/scan_archive.py <报告目录>        # 预检：类型/可用性/下一步
```

## 新机构接入（**三步，一条命令起步**）

```bash
# ① 诊断 + 求解：页型清单 + 候选结构 + 模板草案 + ≤10 条待确认问题（每条带"无人应答时的默认代入"）
python3 engine/tools/onboard_scan.py <样本1.pdf> <样本2.pdf> --org NEW-001 --name "机构名" --search
# ② 验收 + 落盘：求解器枚举候选变体（噪声锚点/分界/单位列/标志列/页标记/栏数）逐款跑验收链，
#    全绿者出线并落盘；多个全绿出"对照卡"；无全绿 → 不落盘（退出码 2）
python3 engine/tools/accept_template.py --pdfs <样本1.pdf> <样本2.pdf> \
    --search --org NEW-001 --install
# ③ 项目名词表：≥0.8 批量通过 + 抽样复核（随机 10% 高置信 + 全部低置信；绝不自动合并）
python3 engine/draft_catalog.py <样本1.pdf> <样本2.pdf> --org NEW-001
```

流程：诊断（页型/候选结构/待确认问题）→ 起草（几何推断；失败时可由 VLM 出道提案）→
**证据链验收**（E1 结构 / E2 回读 / E3 双实现交叉 / E4 旗标交叉 / E5 语义形状 / E6 项数基线）→
**全绿才 `--install` 落盘**。有问题时产出**对照卡**（PDF 行原文 vs 引擎读数 vs 独立复核）与
**可执行修法建议**；对照卡只需要回答"这两个读数一样吗"，**不需要医学知识**。

* **样本数**：建议 2–3 份同年报告。只给 1 份也能跑，但机构专属锚点（`required_anchors`）
  要过"出现在 ≥2 份样本"的校验 → 会留空并写进待确认问题；补一份重跑即自动补上。
* **同类机构**（同厂商版式族）：`checkup/layout_family.py` 按相似度提名最像的模板
  （列序 + 表头词 + 页型分布；**低于硬阈值 → 强制全量初始化**），命中则**差量生成**。
* **首见报告自动验收**：`python3 engine/tools/first_sight.py <新报告.pdf> --package "套餐名" --write-baseline`
  （绿 → 归档并写**分套餐基线**；红 → 报警，不静默入池）。
* 验收**档案写在用户产物目录**（`<out>/accept/<org>/<org>.accept.json`，含决定清单/待确认问题/模板 sha1）；
  **模板**才进包内 `catalog/templates/`。加载器会自动跳过 `*.accept.json` / `*.asset.json` / `bundle.json`
  （都不是模板），无需手工清理。

### VLM 的用法与边界（默认 = **跑 skill 的 agent 本人**）

VLM 只做两件事：**起草提案** 与 **第二读者**（独立读一遍用于交叉比对）。
**它的读数永不写进产物** —— 出数的始终是确定性引擎（可复现/可审计/不编造）。

默认路径**不需要任何配置**：agent 本人看图作答（两相流程）——

```bash
python3 engine/tools/accept_template.py --pdfs … --org NEW-001 --vlm
# ① 首次运行生成工作单：work_order.md + clips/*.png + answers.json.example
# ② agent 读行图 → 把答案写进 answers.json（键=文件名）
# ③ 重跑同一条命令 → 验收链自动比对
```

**什么时候该用它**：第二读者（E7）的价值是把"引擎读数"与"另一双眼睛"交叉比对。
对**干净的文字层 PDF**，列位核对已由 E1–E6（尤其 E3 双实现交叉）在文本层内覆盖，
默认**不需要**开；对**降级输入**（疑似 OCR 叠层等，文本层可能 ≠ 纸面）则**强烈建议**开。
生成工作单时会先提示"**只试读第一张**"——读得了再逐张作答；读不了就停下。

若 agent 的模型**不支持图像输入**：skill 会要求它**明确告知用户**（切换到支持视觉的
模型，或配置外部端点），⛔ 不许静默跳过这道复核。外部端点（OpenAI 兼容）配置：

```bash
export CHECKUP_VLM_PROVIDER=openai
export CHECKUP_VLM_BASE=http://127.0.0.1:11434/v1   # 可用本地模型（Ollama 等）
export CHECKUP_VLM_MODEL=<模型名>
python3 engine/tools/accept_template.py --pdfs … --vlm --allow-network
```

外发默认**不联网**：只有显式 `--allow-network` / `CHECKUP_VLM_ALLOW_NETWORK=1` 才会发送，
且发送的只是**渲染后的页面截图**（脚本会打印"发什么、发到哪、用什么模型"）。
另有 `CHECKUP_VLM_PROVIDER=replay` 用于离线回放（测试/演示）。

## 安装

```bash
# 1) 放到你的 skills 目录（或任意目录，命令里用 SKILL= 指过去即可）
#    例：~/.agents/skills/medical-report-interpretation/

# 2) 装唯一第三方依赖（PyMuPDF）
python3 -m pip install -r engine/requirements.txt

# 3) 预检 + 合成 demo（不需要任何自有数据）
python3 engine/tools/preflight.py
python3 scripts/eval_synth.py --out /tmp/eval_synth
```

要求：Python ≥ 3.9；**仅支持带文字层的电子版 PDF**（无 OCR）。

## 工具清单（engine/tools/ 与 scripts/）

| 工具 | 用途 | 什么时候用 |
|---|---|---|
| `tools/preflight.py` | 环境预检（Python/PyMuPDF/模板/输出目录/只读守卫） | **开工前第一件事** |
| `tools/onboard_scan.py` | 新机构入库诊断：页型清单 + 候选结构 + 草案 + ≤10 条待确认问题（`--search` 顺带求解） | 换机构 |
| `tools/accept_template.py` | 模板验收链；`--search` = 求解器（枚举变体取全绿者）；`--vlm` = 第二读者 | 换机构 / 改版 |
| `tools/first_sight.py` | 首见报告自动验收 + 分套餐基线 | 新报告入池 |
| `tools/write_checklist.py` | 写作前置自检（规则从对账链自动提取） | **动笔写报告之前** |
| `tools/spot_check.py` | 抽检单（≤5 条：机器决定+依据+对照原文页） | 交付时 |
| `tools/draft_template.py` · `build_catalog.py` | 离线起草 / 重建词表（**维护动作**，会写包内 `catalog/`） | 维护者 |
| `scripts/ask_queue.py` | 问诊队列（每条带"无人应答时的默认代入"） | 解析之后、写报告之前 |

## 使用

```bash
# 自己的报告：--src 指向报告目录（含 PDF），--out 指向用户工作目录
python3 engine/run_narrative.py --src <报告目录> --out <工作目录>
python3 engine/run_normalize.py --src <报告目录> --out <工作目录>
python3 engine/run_pipeline.py  --src <报告目录> --out <工作目录>
```

之后按 `QUICKSTART.md` 的 ④ 走底稿 → 报告 → 两图 → 对账。完整流程与门槛见
`SKILL.md`；报告的读取/理解规则见 `references/`。

## 卸载

纯目录制品：删除本目录即卸载。运行期不写系统目录、不装服务、不留后台进程。
产物只在 `--out` / `CHECKUP_OUT` 指向的用户工作目录里。

## 权限与环境变量

> **总表（单一来源）**：代码里读到的每个环境变量都在下面三档里；`SKILL.md` 的 frontmatter
> 只列面向用户的运行时项并指向这里 —— 避免两处声明漂移。分档含义：**运行时**=用户可能会设；
> **开发/测试**=作者本机用；**内部/调试**=**勿设**（改了行为可能不可预期）。

**运行时（面向用户）**

| 变量 | 指向 | 缺省 |
|---|---|---|
| `CHECKUP_SRC` | 报告目录（含 PDF） | 数据根的报告目录（`CHECKUP_REAL_DIR` → 探测 `reports/` 或 `*体检报告*`）；无处可寻则报错、退出码 2 |
| `CHECKUP_OUT` | 运行期产物目录 | 引擎：`<数据根>/checkup-engine/out`；对账层：`<数据根>/体检解读_out`；无数据根时均为 `<当前目录>/体检解读_out` |
| `CHECKUP_FIXTURES` | 数据根（自备报告/引擎产物/私有模板） | 从当前目录/引擎上级向上找 `.checkup-root` 标记文件；找不到即无数据根（功能不受影响） |
| `CHECKUP_TEMPLATES` | **你的机构模板目录**（私有模板，不进分发包） | 探测 `<数据根>/templates` 与 `<数据根>/checkup-engine/catalog/templates` |
| `CHECKUP_LIB` | 体检推荐指引文件库（体积大，**不随包**） | 无（库不可达时如实回答"资料不在本机"） |
| `CHECKUP_PROFILE` | 个体信息（吸烟/用药/家族史；问诊产物） | 无（运行期传入，**不落盘**）；`--profile` > 本变量 > 数据根 `checkup-engine/profile.json` |
| `CHECKUP_CONSTANTS` | 常量登记簿附加文件（问诊数值等） | 数据根 `constants.user.json`（存在即用，附加为「用户登记」） |
| `CHECKUP_VLM_PROVIDER` | VLM 通道：`agent`（默认，跑 skill 的 agent 本人读图）｜`replay`｜`openai`｜`none` | `agent` |
| `CHECKUP_VLM_BASE` | 外部 VLM 端点（OpenAI 兼容，如 `http://127.0.0.1:11434/v1`） | 空（仅 `openai` 通道需要） |
| `CHECKUP_VLM_MODEL` | 外部 VLM 模型名（会记进验收档案） | 空 |
| `CHECKUP_VLM_KEY` | 外部 VLM 端点 API Key（**本地模型多半不需要**） | 空 |
| `CHECKUP_VLM_ALLOW_NETWORK` | 允许把**页面截图**发给外部端点（等价 `--allow-network`；默认不发） | 空或 `0` = 不联网 |

**开发/测试用（作者本机；普通用户不必设）**

| 变量 | 指向 | 缺省 |
|---|---|---|
| `CHECKUP_ENGINE` | 对账层定位引擎目录 | —（自动找 `<上一级>/engine` → `<上一级>/checkup-engine`） |
| `CHECKUP_REAL_DIR` | 覆盖数据根内"报告目录"探测（相对数据根；绝对路径亦可） | —（探测 `reports/` 或 `*体检报告*`） |
| `CHECKUP_SYNTH_DIR` | 合成回归集目录（`--case synth` 用） | —（`<数据根>/research/testdata` → 随包 `examples/testdata`） |
| `CHECKUP_VLM_REPLAY` | `replay` 模式的预录响应文件（离线回放/演示） | —（仅 `CHECKUP_VLM_PROVIDER=replay` 需要） |

**内部/调试（勿设）**

| 变量 | 指向 | 缺省 |
|---|---|---|
| `CHECKUP_VLM_MAX_ROWS` | 第二读者最多复核行数（内部调参） | `12` |
| `CHECKUP_VLM_ANSWERS` | agent 模式 answers.json 路径（内部预留；生产路径由验收工作单目录覆盖，实际不生效） | 空 |
| `PYTHONIOENCODING` / `PYTHONUTF8` | **由工具写入子进程环境**（强制 UTF-8），非用户配置项 | 工具自设（`utf-8` / `1`） |

> 个体信息字段规范：`contracts/profile.schema.json`；把自己的数据目录设为数据根：
> `touch <数据目录>/.checkup-root`（或设 `CHECKUP_FIXTURES`）。

**数据边界**：本包**运行期只读** —— 包里只有代码与数据定义，不含任何个人数据。
报告 PDF、`profile.json`、引擎产物、四张底稿、报告与两图一律写用户工作目录；
脚本对"写入 skill 目录"直接报错。**默认不联网、无遥测**。
（例外：`engine/draft_template.py`、`engine/build_*.py` 等维护工具会把模板/词典
写进包内 `catalog/` —— 那是维护动作；运行期管线不会写包。）

### 云服务

**默认全程本地**。任何云服务（含 OCR 云 API）必须**每次先向用户说明并取得同意**，
且仅限已彻底脱敏的样本；真实报告原件不出本机。

## 限制（如实说明）

- **无 OCR**：扫描件/翻拍件会被如实报出"内容未被分析"，不做图像识别。
- **版式匹配不上就停下**：每套机构模板都必须先起草并**逐项核对**才可用；
  这保证"读不懂绝不猜"，代价是新机构首次接入需要人工过一遍模板。
- **单位只判等价、不换算**：不同单位（如 mg/dL 与 mmol/L）不会被自动换算；
  位置轴照当年机构区间判定，组合/分层评估对不等价的年份**拦下**（报"不可评估"）。
  换算是登记制、需出处（`reconcile/loaders.py` 有示例）。
- **指引库不随包**：`CHECKUP_LIB` 指的知识库（约 317MB / 334 个文件：中国/日本/韩国/
  新加坡/港台/WHO/NICE/USPSTF 等官方文件）因**体积与再分发约束**不随包发布，
  请按 [`references/library.md`](references/library.md)（结构 + 来源渠道 + 自备步骤）自行获取；
  库不可达时，"某国标准"类问题会如实回答"资料不在本机"，不凭记忆作答。
- 规则库覆盖**报告里实际出现的项目**（中国口径为主）。

## 示例

- 合成数据（纯虚构，可直接公开）：[`examples/testdata/`](examples/testdata/)
- 报告形态基准（v6.3）：[`examples/report/样例_体检报告解读_合成数据_v2.md`](examples/report/样例_体检报告解读_合成数据_v2.md)
- 最小 eval（一条命令验证引擎 + 期望特征 + 对账链）：`python3 scripts/eval_synth.py`

## 许可与免责

- 本包以 **MIT** 许可发布（见 `LICENSE`）。
- ⚠️ 依赖 **PyMuPDF** 采用 **AGPL-3.0 / 商业双授权**：个人本地使用按 AGPL-3.0；
  对外分发或商用前请自行确认许可条件。
- **免责声明**：本工具输出用于健康教育与就医决策支持，**不构成医疗诊断**，
  也不替代医生面诊；如有急症请立即就医。
