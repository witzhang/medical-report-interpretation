---
name: medical-report-interpretation
description: 当用户发来体检报告 PDF（1 份或多年多份）要求解读、多年对比、复核或生成健康管理建议时使用；也用于回答"我的指标怎么样""两年报告对比""这个指标要不要紧""上次和这次比变化了吗"这类问题；用户提到体检报告、化验单、彩超/CT 结论、指标趋势、复查建议、健康画像时同样适用。输出《体检报告解读与健康管理建议》（健康画像两图、按级别分节的卡片、行动清单、两个附录），事实性判断全部由脚本产出并对报告逐条数据对账。换机构/版式不匹配时停下报错，绝不硬套别家版式交出"没发现问题"的报告。
license: MIT
version: 1.1.0
compatibility: >-
  Python >= 3.9；唯一第三方依赖 PyMuPDF（pip install -r engine/requirements.txt）；
  仅支持带文字层的电子版 PDF（无 OCR）；默认纯本地运行、不联网。
metadata:
  display_name: "Medical Report Interpretation（体检报告解读）"
  env_CHECKUP_FIXTURES: "数据根（自备报告/引擎产物/基准报告）；不设也能跑合成 demo。也可在数据目录放 .checkup-root 标记文件（自动向上查找）"
  env_CHECKUP_TEMPLATES: "你的机构模板目录（私有模板不进分发包）；不设时探测 <数据根>/templates 与 <数据根>/checkup-engine/catalog/templates"
  env_CHECKUP_LIB: "体检推荐指引文件库（体积大，不随包）；库不可达时如实答『资料不在本机』，不得凭记忆作答"
  env_CHECKUP_OUT: "运行期产物目录；缺省 <数据根>/checkup-engine/out（引擎）或 <数据根>/体检解读_out（对账层）"
  env_CHECKUP_PROFILE: "个体信息（吸烟/用药/家族史；运行期问诊产物，**不落盘进 skill**）；字段规范 contracts/profile.schema.json"
  env_CHECKUP_CONSTANTS: "常量登记簿附加文件（问诊数值等）；附加为『用户登记』，不覆盖引擎内置"
  env_CHECKUP_SRC: "报告目录（含 PDF）；三件套也可用 --src 指定。全量环境变量总表（含开发/内部项）见 README「权限与环境变量」"
  env_CHECKUP_VLM_PROVIDER: "VLM 通道：agent（缺省，agent 本人读图）| replay | openai | none；openai 另配 BASE/MODEL/KEY，且须 --allow-network 或 CHECKUP_VLM_ALLOW_NETWORK=1 才联网。全量总表见 README"
  env_note: "环境变量**总表**以 README 为准（运行时/开发用/内部三档）；本处只列面向用户的运行时项，避免两处声明漂移"
  output: "交付物 = <报告>.md + <同名>_merge_diagram.svg + <同名>_causal_graph.svg + 对账_<名>.md；另有**可选读**的三份审计件：抽检单_<名>.md（≤5 条：机器决定+依据+对照原文页）、写作前置自检_<名>.md、accept/ 验收档案与资产记录（含决定清单/模板 sha1）。全部写用户工作目录"
  disclaimer: "本工具输出用于健康教育与就医决策支持，不构成医疗诊断，也不替代医生面诊；如有急症请立即就医。"
---

# 体检报告解读（Medical Report Interpretation）

把一堆体检报告 PDF + 几句问诊，变成一份**给人看的完整解读报告**，并对报告做逐条数据对账。

**三条不可越界的线**（全流程有效）：

1. **脚本算事实，agent 写话**。数值 / 比较 / 判定 / 缺口 / 状态分类 → 脚本产出；
   含义 / 建议 / 人话 → agent 写。**报告里的事实性判断只能来自四张底稿**，
   ⛔ agent 不许看 CSV 手工数"缺/有/涨了/降了"。
2. **脚本说不行就不行**。版式不匹配 → 非零退出、中止后续所有层；
   报告定稿 = **对账全绿 + 未追踪数字逐条有交代（尽量为 0；手算/换算的须写明算式与来源）+ 结构回归通过 + 问诊必问项齐备 + 双跑级别一致**。
   不通过不许交付。
3. **级别看的是"你现在要不要做事"，不是"机构让多久查一次"**——机构给的复查频率（如每半年）
   **本身不构成升级理由**；级别必须与脚本产物一致，调档必须在卡片写 `（调档：<理由>）`。

**另有三条硬规则只写在细则里，这里点名**（完整口径见 `references/workflow.md` / `references/report-form-v6.4.md`）：

1. **危急值 A/B 类与报告的"红"不是一回事**：A/B 类**另立**，命中时**中断常规流程、置顶直报**；
   报告里**必须**有「本报告的"红"不等于官方 A/B 类危急值」这句声明。
2. **问诊时机**：解析完成**之后**、写报告**之前**；⛔ **不许一上来就问**。
3. **分档不许折叠**：`undetermined` / `not_assessable` / `not_applicable` 不许并进绿色。

## 开工前两步（**做完这两件，再进下面的自动流程**）

1. **环境预检**：`python3 $SKILL/engine/tools/preflight.py`
   （Python / PyMuPDF / 模板可加载 / 输出目录 / 只读守卫五项；不通过会给"下一步跑什么"）。
2. **读全流程细则**：`references/workflow.md`（约 1.7k tokens，**本任务必读，只需读一次**）。
   ⛔ **未读它不得进入阶段 1（问诊）** —— 阶段顺序（摄入→问诊→复算→判读→报告→对账）、
   问诊时机、A/B 类危急值处置**只写在那里**；跳读**不会报错**，但会静默走偏
   （下面的"自动流程"只是**入口预检**，不等于全流程细则）。

## 收到文件后的**自动流程**（不要问"要不要先扫一遍"）

1. **无条件先跑入口预检**：三件套（`run_narrative/run_normalize/run_pipeline --src …`）**自带**输入预检 ——
   不可用（扫描件/加密/OFD/Word/Excel/图片）会**非零退出**并打印"原因 + 下一步"；
   **疑似 OCR 叠层**（数字可能来自图像识别）会要求 `--allow-degraded` 才继续。
   目录里还有非 PDF 时，再跑 `scripts/scan_archive.py <目录>` 看全量清单。
2. 版式未注册（退出码 2）→ 走**新机构接入三步**（见「换一家机构 / 新案例」节）：
   诊断求解 `onboard_scan.py --search` → 验收落盘 `accept_template.py --search --install`。
3. 验收链要"第二读者"时（`--vlm`），**默认由你（agent）本人担任** —— 见下节。
   何时才需要：**降级输入**（疑似 OCR 叠层/扫描件类）建议开；**干净文字层 PDF** 默认
   不需要（E1–E6 已在文本层内覆盖列位核对）。

## VLM 通道：默认由**你（agent）本人**担任（不是先配外部端点）

模板验收的 E7「第二读者」与"起草提案"都需要看图。默认路径是**你自己读图作答**，
外部端点（`CHECKUP_VLM_*`）只是备选：

```bash
python3 $SKILL/engine/tools/accept_template.py --pdfs <样本…> --org NEW-001 --vlm
# 第一次运行会生成"工作单"：work_order.md + clips/*.png + answers.json.example
# ① 你读 clips/ 下的行图（Read 工具即可）→ ② 把答案写进 answers.json（键=文件名）
# ③ 重跑同一条命令 → 验收链自动接着比对
```

**硬规矩（四条）**：

1. **先试读一张行图**。若运行环境返回"模型不支持图像输入"之类的提示
   （例如 `Media omitted … model does not support image input`）——
   **立刻如实告知用户**，原话照抄这条建议：
   > 「当前模型不支持图像识别，模板验收的『第二读者』无法执行。
   > 请切换到**支持视觉的模型**，或配置外部端点：
   > `CHECKUP_VLM_PROVIDER=openai CHECKUP_VLM_BASE=<OpenAI 兼容地址> CHECKUP_VLM_MODEL=<模型>`
   > （默认不联网；加 `--allow-network` 才会把**页面截图**发出去）。」
2. ⛔ **不许静默跳过** E7：读不了图要么提示用户（上面），要么让用户显式放弃
   （不加 `--vlm` 即为"本轮不做第二读者"，验收报告里会写明这是可选跳过）。
3. **先只试读一张**（工作单会点名第一张）：确认能看图再逐张作答，别把整批读完才发现读不了。
4. **VLM 的读数永不写进产物**：它只用于"与引擎读数比对"，不一致 → 出一张对照卡交人看。
   出数的永远是可复现的确定性引擎。

## 何时读哪个文件（渐进披露）

| 场景 | 读 |
|---|---|
| **开工前（先做）** | `references/workflow.md`（六阶段细则：摄入→问诊→复算→判读→报告→对账）—— ⛔ **未读不得进入阶段 1** |
| **N≥2 个体检日期**（脚本报出 ≥2 个日期） | `references/trend-rules.md` —— **在写任何"趋势"字样之前必读** |
| 写报告（形态/章节/卡片/语气） | `references/report-form-v6.4.md` · `references/card-fields.md` · `contracts/report_template.md` · `contracts/agent_rules.md` —— ⛔ **未读 report-form 不得动笔** |
| 事实/数字从哪来、怎么对账 | `references/reconciliation.md` |
| 查指南/更新阈值/回答他国口径 | `references/knowledge-discipline.md` |
| 问诊怎么问、**没人回答怎么办** | `references/unattended-intake.md`（无人值守口径：默认代入 + 敏感性区间；流程不阻塞等人） |
| 用户问"某国标准"、要知道资料库在哪 | `references/library.md`（结构与来源清单；库不可达时如实说"资料不在本机"） |
| 用户给的是**扫描件/翻拍件** | `references/scan-channel.md`（P5 触发；先读再动手） |
| 报告形态基准（**新案例先看实例**） | `examples/report/样例_体检报告解读_合成数据_v2.md`（随包合成样例） |

## 命令速查

```bash
SKILL=~/.agents/skills/medical-report-interpretation   # 按你的安装位置替换
OUT=${CHECKUP_OUT:-<用户工作目录>/体检解读_out}

# ⓪ 环境预检 + 合成 demo + 输入预检（详见 QUICKSTART.md）
python3 $SKILL/engine/tools/preflight.py
python3 $SKILL/scripts/eval_synth.py --out /tmp/eval_synth
python3 $SKILL/scripts/scan_archive.py <报告目录>          # 类型/可用性/下一步；不可用要停下

# ⓪·新机构接入：诊断 → 起草/求解 → 证据链验收 → 落盘（**三步，见「换一家机构 / 新案例」节**）
python3 $SKILL/engine/tools/onboard_scan.py <样本.pdf …> --org NEW-001 --name "机构名" --search
#    诊断器：页型清单 + 候选结构 + 草案 + ≤10 条待确认问题（--search = 顺带跑求解器）
python3 $SKILL/engine/tools/accept_template.py --pdfs <样本.pdf …> --search --org NEW-001 --install
#    求解器：枚举候选变体逐款跑 E1–E6，全绿者出线并落盘；无全绿 → exit 2（不落盘）
python3 $SKILL/engine/tools/accept_template.py --pdfs <样本.pdf …> --template <模板.json> --vlm
#    （--vlm：由你本人读图当"第二读者"：首次出工作单，作答后重跑；外部端点见下）
python3 $SKILL/engine/draft_catalog.py <样本.pdf …> --org NEW-001   # ③ 项目名词表（分层确认）
python3 $SKILL/engine/tools/first_sight.py <新报告.pdf> --package "套餐名" --write-baseline
#    首见报告自动验收：绿 → 归档 + 写分套餐基线；红 → 报警（防"忘了跑验收"）

# ① 摄入 + 分析（同一个 --src/--out；版式不匹配 → 非零退出，停下报错）
python3 $SKILL/engine/run_narrative.py --src <报告目录> --out $OUT
python3 $SKILL/engine/run_normalize.py --src <报告目录> --out $OUT
python3 $SKILL/engine/run_pipeline.py  --src <报告目录> --out $OUT   # 个体信息：--profile / $CHECKUP_PROFILE

# ② 四张底稿（新案例；回归用 --case real|synth）
python3 $SKILL/reconcile/gen_artifacts.py --engine-out $OUT --case-name <名> \
  --profile <个体信息.json> --out $OUT/artifacts/<名>   # --profile 可换 CHECKUP_PROFILE

# ③ 出图（agent 只改 spec，不碰坐标）
python3 $SKILL/figures/merge_map_svg.py <名>_merge_spec.json     # 主图：归并信息图
python3 $SKILL/figures/causal_svg.py    <名>_causal_spec.json    # 附图：因果归并图

# ③·写作前置自检（C5）：从对账链**自动提取**必须/禁止规则，写作前先跑一遍
python3 $SKILL/engine/tools/write_checklist.py --report <报告.md> \
  --specs <名>_merge_spec.json <名>_causal_spec.json --out $OUT/写作前置自检_<名>.md

# ④ 对账与校验（**定稿门槛：不通过不许交付**）
python3 $SKILL/reconcile/reconcile.py --artifacts $OUT/artifacts/<名> --engine-out $OUT --case-name <名> \
  --report <报告.md> --specs <名>_merge_spec.json <名>_causal_spec.json --out $OUT/对账_<名>.md
python3 $SKILL/scripts/validate_report.py <报告.md> --specs <两张 spec>   # 结构回归 + 两图稀疏度
python3 $SKILL/scripts/check_gaps.py <报告.md> --artifacts $OUT/artifacts/<名> --out $OUT/缺口对账_<名>.md
python3 $SKILL/scripts/validate_rules.py                         # 规则库自检
python3 $SKILL/scripts/trace_number.py --report <报告.md> --artifacts $OUT/artifacts/<名> \
  --engine-out $OUT --pdf-dir <报告目录> --sample 5 --out $OUT/抽样回读_<名>.md   # 抽样回读（回原 PDF 页）

# ⑤ 摄入前先扫一遍（阶段0；读不懂就停下报错）＋ 问诊队列（脚本推断）
python3 $SKILL/scripts/scan_archive.py <报告目录>
python3 $SKILL/scripts/ask_queue.py --engine-out $OUT --out $OUT/待问诊.md --check
python3 $SKILL/scripts/repeatability_check.py            # 双跑级别一致（可复现性）
# ⑤·抽检单（C3）：交付物附 ≤5 条（机器决定 + 依据 + 对照原文页）—— 人只做事后抽检
python3 $SKILL/engine/tools/spot_check.py --engine-out $OUT --out $OUT/抽检单_<名>.md
# ⑥ 异常输入演练（P4；8 种情形逐条过）
python3 $SKILL/scripts/edge_cases.py
```

## 非零退出怎么办（**按"工具 + 退出码"对号入座**）

⚠️ 同一个数字在不同工具里含义不同 —— 先看是哪条命令报的，再按下表处置。
（表按**一行一个「工具 + 码」**排列；**码 = `—` 的行与具体退出码无关**。「维护工具」不在本表范围：`health.py` / `audit_docs.py` / `mutate.py` / `triage_mutants.py` —— 按各自 `--help` 与输出处置。）

| 工具 | 码 | 症状 | 原因与处置 |
|---|---|---|---|
| **引擎三件套**（`run_narrative` / `run_normalize` / `run_pipeline`） | 2 | ① `未能识别该报告版式（机构/版式未注册）` ② `报告目录不存在` / `报告目录里没有 PDF` ③ 输入预检未通过（扫描件/加密/OFD/图片…）或**疑似 OCR 叠层**未加 `--allow-degraded` | ① **这是特性**（宁可停下，也不硬套别家版式）→ 走**新机构接入三步**（见「换一家机构 / 新案例」节）；已有模板则放进 `<数据根>/templates/` ② 用 `--src <报告目录>`；或设 `CHECKUP_SRC` ③ 按屏幕上的"下一步"处理（导出 PDF / 去机构 App 下电子版）；降级输入确要继续再加 `--allow-degraded` |
| **`accept_template.py`**（模板验收） | 1 | `BLOCK` —— 模板与这批 PDF 不匹配 | 按对照卡/证据修模板或样本后重跑（不予落盘） |
| **`accept_template.py`**（模板验收） | 2 | `REVIEW`（有需人工确认项）或 `--search` **无全绿候选** | REVIEW：看对照卡确认后 `--force`；无全绿：看求解记录的试跑表 + 待确认问题，修完重跑 |
| **`accept_template.py`**（模板验收） | 3 | 用法错误（如 `--search` 与 `--template` 互斥）/ 输入不可用 | 按提示改参数；输入问题见上面"引擎三件套"那行 |
| **`onboard_scan.py`**（入库诊断） | 1 | 连页型都扫不出 | 版式完全不认识 → 看诊断输出，必要时人工写模板 |
| **`preflight.py`**（环境预检） | 1 | 有检查项未通过（未装 PyMuPDF / 模板加载失败 / 输出目录不可写…） | 按屏幕上的"处置"逐条修；全过时退出码 0 |
| **`first_sight.py`**（首见验收） | 1 | 有报告验收不通过 | 看是哪条证据不绿（改版/抽漏在这一刻被抓） |
| **`first_sight.py`**（首见验收） | 2 | 该机构**还没有模板** | 先走新机构接入三步 |
| **`write_checklist.py`**（写作前自检） | 2 | 有告警（如缺 A/B 类声明、缺口写成"正常"） | 按清单逐条改**再动笔/再定稿**；提示（tip）不阻塞 |
| **任何命令** | 2 | `✗ 模板加载失败：<文件名>…` | 模板目录里有非模板 json（如手工放的笔记）→ 按报错点名的文件**移出**即可；`*.accept.json` / `*.asset.json` / `bundle.json` 会被自动跳过 |
| **任何命令** | — | `import fitz` 失败 | `python3 -m pip install -r $SKILL/engine/requirements.txt` |
| **任何命令** | — | `✗ 产物目录不能落在 skill 目录内` | 用 `--out` / `CHECKUP_OUT` 指向用户工作目录 |
| **任何命令** | — | 单位"不一致/不可比"提示 | 引擎**只判等价、不换算**（设计如此）：位置轴照当年机构区间判；组合/分层项如实报"不可评估"；换算是登记制、需出处 |
| **任何命令** | — | OFD / Word / Excel / 图片 | 暂不支持 → `scan_archive.py` 会逐类给出出路；不要硬转格式后当原生件跑 |

## 换一家机构 / 新案例（**三步，一条命令起步**）

```bash
# ① 诊断 + 求解：页型清单 + 候选结构 + 模板草案 + ≤10 条待确认问题（每条带"无人应答时的默认代入"）
python3 $SKILL/engine/tools/onboard_scan.py <样本1.pdf> <样本2.pdf> --org NEW-001 --name "机构名" --search
# ② 验收 + 落盘：求解器枚举候选变体（噪声锚点/分界/单位列/标志列/页标记/栏数）逐款跑 E1–E6，
#    全绿者出线；多个全绿出"对照卡"交人定夺。无全绿 → 不落盘（exit 2）
python3 $SKILL/engine/tools/accept_template.py --pdfs <样本1.pdf> <样本2.pdf> --search --org NEW-001 --install
# ③ 项目名词表：≥0.8 批量通过 + 抽样复核（随机 10% 高置信 + 全部低置信）；产物 confirmed=false
python3 $SKILL/engine/draft_catalog.py <样本1.pdf> <样本2.pdf> --org NEW-001
```

**样本数：建议 2–3 份同年报告。** 只给 1 份也能跑，但"机构专属锚点"（`required_anchors`）
要过"出现在 ≥2 份样本"的校验 → 会**留空**并写进待确认问题（路由少一道机构闸门）；
补一份样本重跑即可自动补上。

**验收档案**（含决定清单/待确认问题/模板 sha1）留在用户产物目录；**模板**才进包内
`catalog/templates/`。之后按常规跑三件套 → `gen_artifacts` → 写报告 → `reconcile`。

**再接入同类机构**（同厂商版式族）：`checkup/layout_family.py` 先按相似度提名最像的模板
（列序 + 表头词 + 页型分布；**低于硬阈值 → 强制全量初始化**），命中则**差量生成**
（只改锚点/表头/纸型/基线）——但差量草案照样要过 E1–E6。

**首见新报告自动验收**（可选，防"忘了跑验收"）：`python3 $SKILL/engine/tools/first_sight.py <新报告.pdf> --package "套餐名" --write-baseline`
（绿 → 归档 + 写分套餐基线；红 → 报警，非零退出）。

**维护工具**（不属运行期流程）：`engine/draft_template.py`（离线起草/`--verify` 回抽）、
`engine/build_catalog.py`（重建词表）——它们会把模板/词表写进包内 `catalog/`，是**维护动作**。

⛔ 绝不用 `--org` 之外的默认模板硬套；⛔ 不用别家机构的版式猜数据。

## 各国资料入口（用户问"某国标准"时）

库在 `$CHECKUP_LIB`（**不随包分发**；含中国/日本/韩国/新加坡/港台/WHO/NICE/USPSTF 等目录）。
**结构与来源清单（含"如何自行获取"）见 `references/library.md`**。
**不可达时如实说"资料不在本机"，不得凭记忆作答**，并说明可从各来源官方渠道自备。

| 国家/地区 | 能回答什么 |
|---|---|
| 中国 ★ | 项目清单、档位界值、A/B 类危急值（现行版） |
| 日本 | 化验值→档位（全库最完整的判定体系；**单位是 mg/dL，不可与本次中国口径混用**） |
| 韩国 / 新加坡 / 香港 | 法定项目、周期、判定基准；血脂/血压/血糖界值、心血管风险评分 |
| 美/英/WHO | 交叉验证（只作参考不作依据） |

⚠️ 取规则前先读各目录 `links.md` 的日期列（历史版与现行版并存）。
回答必须带出处（指南全称 + 年份 + 章节）+ 声明"**不可与本次中国口径混用**"；
报告里**不出现**国际对照章。

## 隐私与数据边界（**运行期硬约束**）

1. **skill 目录运行期只读**：任何产物（`--out`、底稿、报告、SVG、spec、对账记录）
   一律写**用户工作目录**；写进 skill 目录 → 脚本直接报错（交付失败）。
   （例外：`engine/draft_template.py`、`engine/build_*.py` 等**维护工具**会把
   模板/词典写进包内 `catalog/`——那是维护动作，不是运行期产物。）
2. **个人数据不进 skill**：报告 PDF、`profile.json`、引擎产物都不在包里。
3. **问诊信息问完即用**：只进当次报告的表头（问诊补充 + 前提假设声明），
   **不落盘、不进日志**。
4. **云服务逐次授权**：默认全程本地；任何云 API（含 OCR 云/大模型）
   必须**每次先向用户说明并取得同意**，且**仅限彻底脱敏样本**——真实报告原件不传。
5. 报告与建议**不给诊断、不给药名、不给剂量**；涉药写"须由 XX 科医生决定"；
   急症信号（红旗）必须建议立即就医，⛔ 不软化。
6. **免责声明**：本工具输出用于健康教育与就医决策支持，**不构成医疗诊断**，
   也不替代医生面诊；如有急症请立即就医。

## 零人工介入完成率（KPI，C4）

**目标**：**零人工介入完成率 = 1** —— 从收到 PDF 到交付报告，**任何步骤都不阻塞等人**。
人的角色是"**事后抽检者**"，不是"流程中的回答者"。

怎么做到（本案例 NXYD 就是这条 KPI 的样例 —— 用户全程零回答、零确认，一次跑通）：

1. **确认点全部降级**为"机器默认决定 + 依据 + 不确定标记"，写进验收档案/报告附录
   （C1：`decisions` 区块）。流程里**没有阻塞式提问**。
2. **问诊是可选增强**：没人回答就按默认代入继续，并把敏感性区间写进报告
   （C2：`references/unattended-intake.md`；`ask_queue.py` 每条都带"默认代入"列）。
3. **枚举类确认交给求解器**（A11）：候选变体逐个跑验收链，全绿者出线 —— 机器自行收敛。
4. **人只保留事后抽检**（C3）：交付物附 ≤5 条抽检单（`engine/tools/spot_check.py`），
   是否查看不影响正确性。
5. **正确性不依赖人**：模板层 E1–E6 + 报告层对账 34/34 + 双跑一致 —— 机械验收说了算。

复现本案例（合成样例亦可，无需个人数据）：

```bash
python3 $SKILL/engine/tools/onboard_scan.py <两份同年样本.pdf> --org NEW-001 --search   # 一条命令：草案+问题清单
python3 $SKILL/engine/tools/accept_template.py --pdfs <样本.pdf …> --search --org NEW-001  # 求解器验收
python3 $SKILL/engine/tools/spot_check.py --engine-out $OUT --out $OUT/抽检单.md        # 抽检单
```

## 边界（别当成已完成）

- **v1 无图像通道**：扫描件/翻拍件会被如实报出"内容未被分析"（P5 触发式开发，见 `references/scan-channel.md`）。
- 规则库当前覆盖**报告里实际出现的项目**（中国口径）；他国口径按需现读原文（库不随包）。
- 公网检索、指南更新、规则版本快照（report snapshot）不在本轮范围。
