# 通用室内装修算量引擎设计

- 日期：2026-10-17
- 状态：已与用户逐节确认（第 1~5 节），待用户评审本文档
- 2026-10-18 增补：§1.4 天正图纸硬伤与归一化预处理链（用户裁定后追加，同属待评审内容）
- 前置：Phase 5c 已封账并推送（commit `a0fc55c`），PDF 语义提取 376 行基准
- 规则依据：`docs/QUANTITY_TAKEOFF_MANUAL.md`（R 系列规则、口径体系、封账基准）

## 0 目标与非目标

### 0.1 本轮目标

1. **把 DXF 几何主源接进 Web 计量管线。** 现状 `process_project()` 只处理图片和 PDF，
   完全没有 DXF 分支（`app/processing.py:59-118`），用户上传 DXF 产不出任何工程量行。
2. **几何成为唯一数值来源。** 第 1/2/3/5 分部的入账数字全部由矢量几何推导并带计算式；
   面积注记（P01 `s.m.`）与清单（BOQ）降级为校验证据。
3. **引擎规则驱动、不为单一样本写特例。** 图层角色、房间名、分组、底图选择一律由规则推导；
   227 的封账数值只存在于测试。
4. **建立业态范例库的接口与第一份内容**，用于行项完备性检查（查漏算）与无量纲合理性检查
   （查错算），不用于提供数值。
5. **修掉 3 个已存在的缺陷**（清单行数当工程量入账、漏项匹配实质失效、合理性阈值表无人调用），
   见第 6 节。

### 0.2 本轮非目标（各自独立立项）

- 第二套真实户型的泛化调优（图层命名不同、单线墙+墙厚标注、单位非 mm 等适配）。
  本轮用合成图纸证明规则本身成立，真实第二户型要等它自己的图纸到位。
- 商场 / 酒店 / 售楼部 / 办公四类范例**内容**（需各自真实图纸+清单才能标定行项与区间）。
- 扫描件/纯图片图纸的视觉识别插件（已批准方向，未排期）。
- 机电计量接通。本轮只保证 MEP 现有链路不破，并把第 7/8 分部的「经验补全」行标清是假设。
- 跨源 IoU 融合（Phase 5d）。

## 1 架构

### 1.1 现状与问题

| 现有模块 | 状态 | 问题 |
|---|---|---|
| `modeling/` | 已封账的离线几何引擎（围合、地面、天棚、墙面、踢脚、IFC 校验） | 完全没接进 Web；`base_extract.py:15` 写死 227 DXF 路径；`ROOM_MAP`、BOQ 列分组是样本期常量 |
| `app/core/cad_parser/` | Web 侧 DXF 读取（`decoration.py` 闭合多边形面积） | 无房间围合、无含墙上界、唯一调用方是 `scripts/phase5b_decoration_recon.py` |
| `app/core/pdf_parser/` | Phase 5c 语义提取，已接入 Web | 正常 |
| `app/orchestrator/mep/` | 机电提取与配准 | 在 Web 管线里零调用点 |
| `app/processing.py` | 主分发 | 无 DXF 分支；第 7/8 分部靠「经验补全」造数 |

`modeling/base_extract` 的围合算法是本轮唯一经过真实图纸验证的几何实现，因此采取
**参数化后搬入**，不重复实现一份：`build(dxf=None, strict_enc=False)`，默认值保持现签名，
使离线链路与 `app/orchestrator/mep/registration.py:room_faces()` 都不破。

### 1.2 新模块 `app/core/takeoff/`

每个单元一个职责，可独立理解和独立测试：

| 文件 | 职责 | 依赖 |
|---|---|---|
| `frames.py` | 按图框/布局/VIEWPORT 切出平面复制簇（天正图常把多套平面叠放在同一模型空间，§1.4）；正式输出图（paper space）优先；标注每簇的判据得分 | ezdxf |
| `layers.py` | 图层角色判定：真墙（平行双线 100~250mm）、门窗洞口线、反坎、房间标签层；展开 INSERT 块内几何；合并 `$TD_AUDIT_*` 孤儿层；**收图探针 `proxy_survey()`（§1.4.1，先于角色判定）** | ezdxf |
| `enclosure.py` | 双线墙缓冲成墙带 + 门洞桥接 → 房间面；产出净面积与含墙外包面积、围合周长、`comp_id` | shapely, `layers` |
| `takeoff.py` | 由房间面 + 用户参数产出各分部行（地面/天棚/墙面/洞口/踢脚），每行带计算式与溯源 | `enclosure`, `quant_table` 行结构 |
| `reconcile.py` | 互验：只往既有行写状态（一致/口径差/冲突/孤证），不产出任何新数字 | — |
| `template.py` | 行项完备性与无量纲合理性判据；三档漏项分档 | `templates/*.json` |
| `coverage.py` | 空间 × 部位覆盖矩阵，产出缺图反馈数据 | — |
| `rows.py` | `Row` → `Measurement` 落库适配（唯一允许 import db 的位置） | `app.models` |
| `templates/` | 业态范例数据（JSON，不是代码常量） | — |

依赖方向单向：`app/processing.py` → `app/core/takeoff` → (`ezdxf`, `shapely`)。
`takeoff` 不得 import `flask`、`db`、`modeling`，保证在无 app context 下可测。

### 1.3 数据源适配器契约（插件形态）

识别能力（CAD 读图 / 图片识别 / PDF 语义）统一为一个契约，便于后续增换插件：

```python
class Source:
    name: str
    def supports(self, drawing) -> bool: ...
    def extract(self, ctx) -> list[Row | Evidence]: ...
```

本轮把三个适配器对齐到 `run()` 同一入口，**不改各自内部算法**：

- `DxfSource`（新）→ 几何主源，产出有值的 `Row`；
- `PdfSource`（包装现有 `_process_pdf_drawings`，`app/processing.py:417`）→ 口径注记与证据；
- `ImageSource`（包装现有光栅分支 `_process_floor_plan` / `_process_elevation`）→ 辅助证据。

### 1.4 天正（TArch）图纸的三项硬伤与归一化预处理链

> 触发原因：本图纸集内多份 DXF 出自天正（TArch / T20 / T8）绘制后导出。收图阶段若不先做一次
> 结构普查，`frames` / `layers` 会在「读到 0 实体的图层」上安静地算出 0 工程量——
> 这是本项目最坏的一类错误（漏算且无痕迹），因此单独立节。

实测取证（探针 `_tz_probe.py`，2026-10-18，对象为 `装修图纸/` 下 20+ DXF / 24 DWG）：

| 图纸 | 版本 / 代码页 | 代理对象 | 块名状况 | 混排 |
|---|---|---|---|---|
| `05.227户型平面图 rev-33.dxf` | AC1032 / ANSI_936 | 0 | 177 种：`A$C…` 50、`*U…` 43、其他 ASCII 83、`$equip_U$…` 1；TOP `A$C64342E2E`×106、`lm`×30、`d-5`×26 | 平面位于 布局1 的 **18 个 VIEWPORT** 与 `A$C5D74690A`×42 图纸块内（模型空间叠放多套复制） |
| `…给排水户型大样图_t3.dxf` | **已做过天正 3 导出** | **84 个 `ACAD_PROXY_ENTITY`，全部落在图层 `DT`** | `$TwtSys$00000125`×42、`$TwtSys$00000131`×17 | — |
| `136、227户型 样板间电气….dxf` | AC1032 | 0 | 125 种：`*U` 125、`A$C` 143、`$TD_AUDIT_GENERATED_(80982)`×57 | 136 与 227 两户型同文件 |

三条结论决定了预处理链的形态：

1. **代理对象 ezdxf 读不到几何，且 T3 导出不保证炸净**（`_t3.dxf` 仍剩 84 个）→ 这必须在产品外解决，
   属**图纸交付要求**（§1.4.3），不是解析器能绕过的坑。应用侧唯一正确行为是命中即降级报错，
   **绝不当空白图层跳过**。
2. **匿名/乱码块名不携带语义**（`*U`、`A$C…`、`$TwtSys$…`、`$equip_U$…(WIN-3PDLNMTE0GS)`
   括号内为机器名/文件名）→ 设备与构件识别按 `(图层, 块名, ATTDEF 默认值, 插入点簇)` 分组，
   **不猜块名含义**；块名只作分组键与溯源字段，不建「块名 → 清单项」映射表。
3. **多楼层 / 多平面混排是几何事实**（布局视口 + 模型空间复制）→ `frames` 必须先切簇再谈图层角色，
   否则同一面墙会被跨复制重复计数（§2.3 打分选主簇）。

#### 1.4.1 收图探针 `layers.proxy_survey()`——Phase 1.2 的第一步，先于底图打分

职责：单次遍历模型空间 + 全部布局，产出 `SurveyReport`（DXF 版本、`$DWGCODEPAGE`、实体类型计数、
**代理对象按图层计数**、块名前缀分类计数、VIEWPORT / 图框簇数、TEXT 尺寸分档）。
**探针不产出任何工程量数字**，只产出状态。

判定与行为（全部写成 `issues`，渲染到清单页「覆盖与缺图」面板）：

| 探针结果 | 行为 |
|---|---|
| 代理对象所在图层**命中**真墙 / 门窗洞口 / 反坎 / 房间标签任一角色 | 该簇整体降级：issue `t20_proxy_on_role_layer`，列出图层名、代理数、被破坏的几何角色，要求按 §1.4.3 重新导出；该分部**不出数** |
| 代理对象只落在标注 / 家具 / 无关图层 | 只披露，不影响算量 |
| 角色图层齐全且零代理，但块名近 100% 匿名（`*U`/`A$C`/`$TwtSys$` 占比 > 0.9） | 正常出数；`remarks` 原样保留块名作溯源；**不建块名语义映射** |
| 同一 DXF 内 VIEWPORT ≥ 2 或图框簇 ≥ 2（多平面混排） | 转 §2.3 四类信息共现打分选主簇；并列则要求人工钉 `Drawing.is_base` |
| 探针自身读不动（非 DXF / ezdxf 异常） | 沿用 §3.4 末行：降级为 issue，不出数 |

#### 1.4.2 与既有裁定的衔接

`docs/QUANTITY_TAKEOFF_MASTER_ARCHITECTURE.md` §12.7 已裁定「**保持块完整、禁止预打散**」，
本节与之不冲突：探针只**读**块名与 ATTDEF 默认值用于分组计数，不展开、不打散；
确需块内几何或块内文字时用 `insert.virtual_entities()` 临时取用、取完即弃，
`rot=270` 图框的取框走 `ezdxf.bbox.extents([ins], fast=False)`（`insert.point` 不可靠）。

#### 1.4.3 交付要求（应用外的硬前提）

天正图必须先由设计师完成归一化，再作为可算量图纸交付：
`文件布图 → 图形导出 → 天正3格式(.dwg)` → 另存 **DXF R2018(AC1032)**，收图探针以
「代理对象数为 0，或仅落在无关图层」为验收门。
本环境 macOS 无 AutoCAD / 天正，`accoreconsole` 仅 Windows，**应用不得指望自动炸代理**，
命中即按 §1.4.1 报错回退给人工。要求全文见 `docs/CAD_PARSING.md` §十。

## 2 接口契约与数据流

### 2.1 入口

```python
run(params: TakeoffParams, sources: list[Source]) -> TakeoffResult
TakeoffResult = {rows: list[Row], coverage: CoverageMatrix, issues: list[Issue]}
Row = dataclass(division_id, label, category, unit, value,
                formula_text, position_desc, remarks,
                source_tag, confidence_level, material_code, check)
```

`run(params, sources)` 取代此前口头确认过的三参形态 `run(params, dxf_paths, pdf_evidence)`：
入参改成 §1.3 的 Source 列表后，DXF/PDF/图片三类识别器走同一入口，增加识别插件不再改签名。

`Row` 是纯数据。落库经 `rows.py:to_measurement(row, project_id, drawing_id)`，
沿用现有 `emit()` 的图标赋值规则（`source_tag_icon` / `confidence_icon` 是实体列，
不显式赋值会把 medium 显示成绿色高置信，见 `app/processing.py:495-507`）。

### 2.2 挂载点与顺序

`process_project()` 新增 **Phase 1.2**，排在 PDF 之前（主源先落）：

1. Phase 1 图片（现有）
2. **Phase 1.2 DXF 几何主源（新）**
3. Phase 1.5 PDF 语义（现有）
4. Phase 2 立面（现有）
5. Phase 3 派生行（现有；受 2.7 抑制规则约束）
6. Phase 3.5 `reconcile.apply()` 打状态
7. Phase 4 经验补全行（现有；按 3.3 标为假设口径）
8. Phase 5 `template.apply()` 漏项与指标判据

### 2.3 定稿底图的锁定

1. 候选：本项目 `filename` 以 `.dxf` 结尾的图纸；
2. `frames.cluster(doc)` 切平面复制簇（模型空间常叠多套平面，必须分簇）；
3. 每簇按**四类信息共现**打分：真墙层 + 房间标签 + 门窗块 + 反坎层，正式输出图（paper space）优先；
4. 最高分簇作主源；其余簇与得分并列都写入 `issues`，不静默丢弃。

人工覆盖：`Drawing.is_base`（Boolean, nullable）——NULL 走自动判定，显式赋值以人工为准。

### 2.4 用户参数

`Project.takeoff_params`（Text, nullable，存 JSON）：
`slab_thickness_mm`、`floor_finish_mm`、`door_height_mm`、`window_height_mm`、
`window_sill_mm`、`ceiling_levels`、`scale_count`（间数/开间数，仅密度判据用）。
净高沿用已有 `Project.default_floor_height`（`app/models.py:118`）。

未填项中，只有门 2100 / 窗 1500 / 窗台 0 是已裁定的长期参数口径可作默认值；
板厚与地面完成厚度**不设产品默认值**（227 用的 100/50 是样本期竖向参数，不得进入产品代码），
且本轮面积类工程量不依赖它们——需要它们的是竖向展开与后续建模派生量，届时未填即标待确认。
所有取默认值的行按 3.3 在计算式里披露。

数据库变更：**一个 Alembic revision，两处 nullable 新列 + 一次值映射 UPDATE**（见 5.2）。
新列均可空、无回填风险；取表结构用 SQLAlchemy inspect，不用 SQLite 专属 PRAGMA。

### 2.5 行落库映射

| 算量表列 | Measurement 字段 |
|---|---|
| 分部（1-8） | `division_id` |
| 项目名称 | `label` |
| 单位 + 工程量 | `unit`，数值按量纲落 `area` / `length` / `count` 之一 |
| 计算式 | `formula_text`（原文照搬，不缩写） |
| 溯源 | `position_desc`（房间+方位+图号）、`remarks`（`layer=` / `comp_id=` / `page=`） |
| 校验 | `data_json['check']`（净 / 含墙 / 注记三口径 + 判据结论） |

`source_tag` 复用现有枚举不扩表：矢量墙线直接围合量出的面积归「图纸实测」🟢
（该标签定义即"直接从图纸读取未经推算"，`app/models.py:24`）；由参数派生的洞口、踢脚、
墙面净高归「几何推算」🟡。

`category` 命名：`dxf_floor / dxf_wall / dxf_ceiling / dxf_opening / dxf_baseboard`，
与现有 `pdf_*` 前缀对齐，互验时来源可辨。

### 2.6 与 PDF 行共存

- DXF 几何行 = 唯一数值主源，有值、进合计；
- P01 `s.m.` 注记行改为 `area=None, unit='注记'`，值放进 `data_json.check`，不进合计；
- PDF 候选分区保持 `area=None / 候选`；房间标签、材料代号、尺寸行保持证据行身份；
- `reconcile` 只写状态，不新增数字：一个数字只能有一个来源。

### 2.7 双主源冲突裁定（已确认）

第 2/5 分部存在 DXF 几何行时，图片派生的墙面/踢脚行**不落库**，只记一条 issue
「图片派生墙面已由 DXF 几何主源取代」；无 DXF 时照旧运行图片派生。

### 2.8 覆盖矩阵

`coverage`：空间 × 部位（地面/天花/墙面/洞口/踢脚），单元格记
`{有源|无源|待参数, source}`。渲染到清单页与报告的「覆盖与缺图」块。
无源单元格**不造行**。

## 3 口径、降级与错误处理

### 3.1 面积口径

| 口径 | 定义 | 用途 |
|---|---|---|
| 净面积 NetArea | 墙内面线围合 | **唯一入账值**（第 1/3 分部） |
| 含墙外包 GrossArea | 墙外面最外线 | 仅作上界，永不入账 |
| P01 `s.m.` 注记 | 含墙/轴线口径 | 仅对照，`area=None` |

判据带：`注记 ∈ [净, 含墙×1.05]` → 口径一致；`> 含墙×1.05` → 标「查(飘窗/围合/口径)」，
**只披露不回填**；`< 净×0.95` → 同样只披露。
开放区无四面墙 → 含墙上界不可定，按净值出，`remarks` 写明原因。

天棚：`天棚 = 地面净面积 − 无标注块`（227 即 163.75 − 1.60 = 162.15），
计算式必须显式写出被扣项，不再出现「天棚面积 = 地面净面积」这种口径谎称。

### 3.2 墙面校验判据的改法

`outputs/model/wall_geometry.md` 的 105~133% 表用的基准本身是错的：它拿
「净墙面 = 毛墙面 − 洞口」去比 BOQ 的实贴展开面（贴砖不扣门洞、含侧边与包管；
浴室三 BOQ 26.60 已超几何毛墙 22.73）。

改法：墙面行保留几何自证算式（`围合周长 × 主吊顶净高 − 洞口`），
对账层**不再拿饰面展开量比总数**，换成无量纲指标。有判定带的只有两项：
项目级墙地比（模板 `ranges`）与踢脚/围合周长比（0.6~1.1，同 `app/api.py:224`）；
每间墙地比、洞口率、含墙率本轮只披露与排序，不设判定带。
展开面差异仅在上述两项越界时提示「可能存在包管/侧边增加」，不给数。

### 3.3 参数假设披露

凡使用默认竖向参数的行：`formula_text` 内写「参数假设(用户未填，取默认 2100)」，
`confidence_level='medium'`；清单页顶部汇总「本项目 N 行使用了默认竖向参数，建议补填」。
用户补填重算后这些行升 🟢。第 7/8 分部「经验补全」行同样按假设口径标注，
明确它不是图纸提取量。

### 3.4 降级矩阵

| 情况 | 行为 |
|---|---|
| 项目无 DXF | 第 1/2/3/5 分部不产几何行；PDF/图片行全标 🔴；`issue=no_dxf_base`「无矢量底图，全部量为待确认，禁止对外报价」 |
| 有 DXF，真墙判据零命中 | **不猜墙**。issue 附候选图层名 + 各图层平行线间距直方图，交人工裁定 |
| 天正代理对象落在角色图层（`ACAD_PROXY_ENTITY`） | §1.4.1 探针命中即该簇降级，issue `t20_proxy_on_role_layer` 附图层名+代理数，要求按 `CAD_PARSING.md` §十 重新导出；**不当空白图层跳过、不出数** |
| 房间标签层缺失/无名 | 按 `comp_id` 出「未命名空间 N，净面积 X m²」行，标 🔴；绝不编房间名 |
| 多簇得分并列 | 不自动选，issue 要求人工钉 `Drawing.is_base` |
| 只有 DXF、无 PDF | 跳过互验，coverage 记 `pdf_evidence=none`，注记列留空而非造 0 |
| 单位非 mm / ezdxf 读失败 | 先按 `$INSUNITS` 试换算；换算后墙厚仍不落 100~250mm → 判单位不明，降级为 issue，不出数 |
| 洞口无法归属（227 实测 42 樘 / 35 成功 / 7 孤儿） | 成功开洞才扣墙面面积；7 个孤儿照常计入第 4 分部**樘数**（直接计数非几何推断），并单列「未归属洞口」行 |
| 业态无范例内容 | 只跑通用 8 分部模板；issue「该业态暂无范例库，漏项检查已降级」；未知 `project_type` 值走同一条兜底 |

### 3.5 错误处理三条硬线

1. **不确定 ≠ 造数**：歧义只能变成「证据行 + 待确认」或一条 issue，不能变成合理数字。
2. **引擎不外抛**：`run()` 捕获全部解析异常收敛为 issue；单张坏图纸不得让整单计量 500
   （沿用 `app/processing.py:445` 的跳过先例）。
3. **人工裁定口不变**：沿用 `MANUAL_PEND` 精确钉行，不做箭头距离泛化；机器重算不得静默推翻已钉行。

## 4 测试策略

前提事实：`装修图纸/` 整目录被 `.gitignore:44` 排除（`git ls-files 装修图纸` = 0 文件），
CI runner 上没有图纸，所以现有依赖真实图纸的测试在 CI 上是 `skipif` 静默跳过
（`tests/test_pdf_semantics.py:293`、`tests/test_takeoff_golden.py:94`）。

| 档 | 内容 | 执行环境 |
|---|---|---|
| **Tier A** 合成图纸 | ezdxf 现场造图：双线墙 200mm 围合三间 + 一门洞 + 房间标签。断言面积等于手算值；300mm 间距判非墙；单线零命中→issue；单位非 mm→换算或降级；无标签→未命名空间；两簇并列→要求钉定。**§3.4 每行降级至少一个用例**（代理降级一行：合成图无真实代理对象，改为注入 `SurveyReport` 桩触发 `t20_proxy_on_role_layer`；含真代理的图纸用例只在本机跑） | CI + 本地 |
| **Tier A+** 脱敏小样 | `tests/fixtures/minimal_flat.dxf`（几 KB，非客户图纸）+ 生成脚本 `scripts/make_minimal_dxf.py` 一并入库可重建。让 CI 能跑真实几何链路而不只跑假数据 | CI + 本地 |
| **Tier B** 227 golden | 地面 163.75 / 天棚 162.15 / 墙面净 437.22 / 踢脚 166.73 / 洞口 42 樘·35 开洞，容差 ±0.01；基准值入 `tests/golden/takeoff_227.json`（数值快照入库，图纸不入库）；PDF 语义 376 行不变 | 仅本地 |
| **Tier C** 模板与互验 | 合成清单 fixture（行项名+单位，物理不含量）；三档判据各一例：抽掉一行→报真漏算；无单位无图源的行项→只许报缺图源；人工排除→记 issue 不算漏。锁死 `auto_fill_missing` 补入行 `area is None and count is None` 且不进分部合计 | CI + 本地 |
| **Tier D** 非回归 + 真实链路 | `tests/test_mep_module.py` 与 `m2_attribution.json` 载入路径不破；`base_extract.build()` 默认参不变；全量 98 passed 为基线；真实 HTTP：登录→上传 DXF+PDF→计量→清单页→xlsx→报告 | 本地 |

执行约束：`venv/bin/python -m flask`（macOS 5000 被 AirPlay 占用，用 5001）；
PG 走 5433 + `127.0.0.1`；**绝不设置 `TEST_DATABASE_URL`**。

**本轮退出判据**：Tier A/A+ 全绿（CI 可证）+ Tier B 四值命中 + Tier C 三档判据与
auto-fill 约束就位 + Tier D 不破。

## 5 行项模板层与业态范例库

### 5.1 职责分工

- **模板层**答「该不该有这一行」（行项 + 单位 + 分部归属）；
- **覆盖矩阵**答「这一行该在哪些房间有量」（空间 × 部位）；
- **几何层**答「这行的数是多少」。

模板与覆盖矩阵都不产数值。

### 5.2 业态枚举改为 5 类（已确认）

新 `PROJECT_TYPES`：`sample_flat` 样板房 / `showroom` 售楼部 / `mall` 商场 / `hotel` 酒店 /
`office` 办公。`project_type` 是自由字符串列（`app/models.py:117`），扩类别不需改表结构，
只需 Alembic 里一次值映射：

```sql
UPDATE projects SET project_type='sample_flat' WHERE project_type='residential';
UPDATE projects SET project_type='office'      WHERE project_type='commercial';
```

`lobby` 从下拉移除，但数据库里已有的 `lobby` 值不改写——未知业态走 §3.4 最后一行兜底。
默认层高沿用原值映射：`sample_flat` 2.80、`office` 3.00、`showroom` 4.00；
`mall` / `hotel` **不给默认值**（没有依据），用户未填净高时相关行按 §3.3 标 🔴 待确认。

`historical_projects` 表本地为空，改造风险低：保留该表作用户上传范例清单的载体，
`summary_json` 只存行项与单位，不存工程量。

### 5.3 模板文件形态

`app/core/takeoff/templates/<type>.json`：

```json
{
  "type": "sample_flat",
  "version": 1,
  "origin": "金融城227样板房工程量清单 · ZS02-A-精装修工程-227户型（列项抽取，工程量已丢弃）",
  "items": [
    {"division": 1, "name": "地面水泥砂浆找平层", "unit": "m2", "material": null},
    {"division": 5, "name": "踢脚线不锈钢饰面", "unit": "m", "material": "不锈钢"}
  ],
  "ranges": {
    "wall_floor_ratio": [2.0, 3.0],
    "baseboard_perimeter": [0.6, 1.1],
    "light_per_area": [0.10, 0.20],
    "socket_per_area": [0.08, 0.15]
  }
}
```

`ranges` 里的四组区间就是现有 `REASONABLENESS_RANGES`（`app/comparison.py:42-48`）的
residential 值原样迁移，**不新增未经验证的阈值**。洞口率、含墙率这类新指标本轮只计算并披露、
参与排序，不设判定带；要设带的必须先由范例库给出依据。
`waterproof_ratio` 需要防水上翻高度（图纸标注或用户输入），本轮无来源，保持不启用并记 issue。

叠加顺序（已确认）：**内置通用 8 分部模板 → 业态范例补细节 → 用户上传清单增补/覆盖阈值**。

抽取脚本 `scripts/build_boq_template.py`：读本地 xlsx（该文件不入库），
输出上述 JSON 入库；读取时即丢弃工程量列与房间列，并断言输出 JSON 不含数值型工程量字段。
这个断言是防泄漏的可执行守卫，进 Tier C。

模板源 sheet 已确认取 `ZS02-A-精装修工程-227户型`（与 `modeling/wall_boq.py` 同一基准）。
注意该 sheet 分部标题只有 一/二/三/五，**缺第 4 分部（门窗在 `户内门` sheet）**、
第 7/8 分部在 `ZS02-B- 精装修水电136、227户型`，抽取范围必须覆盖这三个 sheet，
否则会误报「门窗、水电全漏」。

### 5.4 漏项三档判据

| 档 | 条件 | 行为 |
|---|---|---|
| 真漏算 | 模板有此一行 + 覆盖矩阵该部位有图源 + 当前无该分部行 | 报警，要求补算 |
| 缺图源 | 模板有此一行 + 该部位无图源（如需大样图才能算的吊顶线条） | 进缺图反馈，不补数 |
| 不适用 | 模板有此一行 + 本项目空间构成不含 | 人工一键排除，记 issue |

模板里的子分组标题行（`地面基层`、`墙柱面瓷砖`、`定制GRG线条1~6` 等计量单位为空者）
在抽取阶段标记 `group_only: true`，不参与真漏算判定。

### 5.5 行项匹配键（取代现有失效匹配）

现有 `_items_match`（`app/comparison.py:309`）指望关键词交集 ≥2，但
`re.findall(r'[一-鿿\w]+')` 把整串连续中文当成**一个** token，实测交集永 ≤1，
实质只剩子串包含——`瓷砖踢脚` vs `【主卧】踢脚线` 判不存在（误报），
`石材地面` vs `客厅石材地面` 判存在（房间维度缺项被掩盖）。

新规则（明确、不模糊）：

1. 归一化：去 `【】()` 前后缀、去空白、去「每增减一遍」等修饰；
2. 行项键 = `(division_id, 材料词, 单位量纲)`；材料词取自受控词表
   （`DIVISIONS` 材料清单 + `BASE_LAYER_RULES` 键 + `PT-/WD-/ST-` 代号）；
3. 命中 = 键相等；仅名称子串包含不算命中（避免房间前缀干扰）；
4. 房间维度是否齐由覆盖矩阵负责，模板层不做房间级判定；
5. 键不相等且无材料词可提取 → 归入「人工裁定」候选，不自动判定、不自动补。

### 5.6 auto-fill 重定义（已确认）

现 `auto_fill_missing`（`app/comparison.py:389`）把 `_find_item_quantity` 返回的
`division_counts`（该分部**行数**）当工程量写进 `Measurement.count`
（`app/comparison.py:409`）——「历史表地面有 13 行」就变成「这项 13 个」，
且带 🟡 中置信直接进清单页与 xlsx 导出。

改为：补入行只写 `label` / `unit` / `division_id` / `category='template_gap'`，
`area`、`length`、`count` 全部 `None`，`source_tag='待确认'`、`confidence_level='low'`，
按钮与文档措辞由「自动补全」改为「加入待算清单」。分部合计不随补入行变化（Tier C 锁死）。

### 5.7 合理性判据接线

`REASONABLENESS_RANGES`（`app/comparison.py:42-48`）定义了 5 个区间却无任何调用点。
本轮把它迁进模板 `ranges` 字段，由 `template.apply()` 按业态取用。
指标一律从几何行自算：墙地比、踢脚/围合周长比、灯具与插座密度（分母为地面净面积，
与现有实现口径一致）。`scale_count`（间数/开间数，§2.4）只服务于「按间/按开间」型判据
（酒店每客房量、商场每铺位量），未填则跳过该类判据并记 issue，不用假定的间数出结论。

### 5.8 接线现状

漏项审计目前只挂在 orchestrator（`app/orchestrator/audit.py:127`），主流程不跑、清单页看不到。
本轮接入点：`process_project()` Phase 5 调用 `template.apply()`，结果写进项目级
`issues`，清单页与报告各增一个「疑似漏项 / 指标越界」面板。

## 6 缺陷修复清单

| # | 位置 | 缺陷 | 处理 |
|---|---|---|---|
| 1 | `app/comparison.py:409` | 分部行数当工程量入账 | §5.6 |
| 2 | `app/comparison.py:326` | 中文 token 化致匹配实质失效 | §5.5 |
| 3 | `app/comparison.py:42-48` | 阈值表无调用点 | §5.7 |
| 4 | `app/processing.py` 第 3 分部 | 天棚公式谎称「= 地面净面积」 | §3.1 |
| 5 | 第 1/3 分部入账 | P01 `s.m.`（含墙口径）入账 203.10 | §2.6 转注记行 |
| 6 | `app/processing.py:59-118` | 无 DXF 分支，上传 DXF 零产出 | §2.2 Phase 1.2 |
| 7 | `outputs/model/wall_geometry.md` | 校验表基准错（净墙比饰面展开面） | §3.2 |

## 7 交付切分

本设计含两组可独立验收的改动，实现计划按里程碑排，不混在一轮里验：

**里程碑 A：几何主源引擎**（§1~§4）
模块 `frames/layers/enclosure/takeoff/reconcile/coverage/rows` + 收图探针与代理降级（§1.4.1）+
Phase 1.2 接线 +
2 处新列迁移 + 缺陷 #4/#5/#6 + Tier A/A+/B/D。
验收：CI 全绿（合成图 + 脱敏小样）+ 本机 227 四值命中 + MEP 不破 + 真实链路出清单。

**里程碑 B：范例库与查漏查错**（§5）
模块 `template` + `templates/` 与抽取脚本 + 业态枚举 5 类迁移 +
缺陷 #1/#2/#3/#7 + Tier C + 清单页「疑似漏项 / 指标越界」面板。
验收：故意抽掉一行能报出真漏算、无图源行项只报缺图源、auto-fill 补入行不改变任何分部合计。

B 依赖 A 的几何行才有比对对象（无几何行时三档判据退化为「全部缺图源」），
因此顺序固定为 A→B；A 完成即可对外交付算量能力，B 属于质量增强。

## 8 决策记录

| 决策 | 结论 | 出处 |
|---|---|---|
| 下一步方向 | 补墙面饰面量主源 | 2026-10-16 用户裁定 |
| 量差异常 | 先查浴室四间 105~133% 根因再入账 | 同上 |
| 面积口径 | 接几何净面积主源，P01 `s.m.` 降级为校验 | 同上 |
| 野心边界 | 「227 生产可用」被纠正为「227 只是验证，应用要通用」 | 2026-10-16 用户纠正 |
| 接线方案 | A 薄适配层 | 2026-10-16 |
| 输入契约 | DXF 硬前提 + PDF 图片识别，相互检验 | 同上 |
| MEP | 只保证不破 + 标清假设口径 | 2026-10-16 |
| 双主源冲突 | 图片派生墙面被 DXF 主源取代时不落库 | 2026-10-17 |
| 模板来源 | 内置通用 + 227 派生样板房模板 + 上传覆盖（三者叠加） | 2026-10-17 |
| 模板 sheet | `ZS02-A-精装修工程-227户型` | 2026-10-17 |
| auto-fill | 只补行项、量置空、标待确认 | 2026-10-17 |
| 查漏范围 | 并入本轮，作为设计第 5 节 | 2026-10-17 |
| CI 护栏 | 提交脱敏小样 DXF 入库 | 2026-10-17 |
| 业态分类 | 改 5 类：样板房/售楼部/商场/酒店/办公，对照范例库查漏查错 | 2026-10-17 |
| 天正 DXF 三硬伤 | 代理对象属**交付要求**（探针命中即降级报错，不猜不当空白）；匿名块名只作分组键不建语义映射；多平面混排交 `frames` 切簇 | 2026-10-18 用户裁定「归一化预处理+交付要求 / 社区技能当参考资料 / 装官方 CAD 批量工具」 |
