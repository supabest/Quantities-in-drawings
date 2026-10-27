# 社区参考：`hwdemtv/tianzheng-dwg-parse`（天正 DWG 解析技能）

> ## ⚠ 信任声明：未经审阅的外部社区内容
>
> 本目录下的 `SKILL.md` 与 `references/pitfalls.md` 是**原样留档的外部社区技能文本**，用途只有一个：
> **DXF 排障时的对照出处**（报错→处理表、参数经验、坑清单）。
>
> - 本文档**不是本项目算量规则的出处**。规则一律以
>   `docs/QUANTITY_TAKEOFF_MANUAL.md`、`docs/CAD_PARSING.md`、`docs/superpowers/specs/` 为准；
>   与本文件冲突时，**以本项目实测为准**。
> - 本项目**未执行**该技能附带的任何脚本（见 3 节），也未运行其安装脚本。
>   文中的「实测」指的是**原作者在中南科研设计中心弱电图上的实测**，样本专业（弱电/机电）
>   与本项目主战场（住宅样板房装修：墙/地/顶/墙面/洞口）**不同**，可迁移性逐条评估见 4 节。

---

## 1 来源与留档信息

| 项 | 值 |
|---|---|
| 上游仓库 | `hwdemtv/tianzheng-dwg-parse` · https://github.com/hwdemtv/tianzheng-dwg-parse |
| 默认分支 / 钉住提交 | `main` @ `c4789e32edebd7a9e7a5f2e071c2c0bef15ad481`（提交时间 2026-09-17T01:11:14Z） |
| 上游星数 / 成熟度 | 1 star，单次提交，**单人单来源** |
| 拉取方式 | 只读抓取 raw Markdown 文本（2026-10-18），逐字节原样保存，未改写正文 |
| 留档文件 | `SKILL.md`（64 行）、`references/pitfalls.md`（129 行） |
| 上游另有 | `scripts/dxfout.py`、`scripts/tz_survey.py`、`scripts/tz_floors.py` — **未拉取、未执行** |
| 发现渠道 | Qoder 官方市场无同名项；skills.sh 未收录该仓库 → 属"仅 GitHub 可考"的社区单来源 |

## 2 它的核心主张（摘要，详情读原文）

1. 天正自定义对象 `ACAD_PROXY_ENTITY` 让 ezdxf **完全读不到内容**，所以解析链必须先归一化：
   `天正3导出 → accoreconsole _.DXFOUT(R2018) → ezdxf 解析 → 逐层统计`。
2. ezdxf 对**任何版本**的 DWG 二进制都报 `OSError: is not a DXF file`，必须先转 DXF。
3. 文件头判格式：`AC1018`=2004/T3，`AC1021+`=2007+/T20。
4. T3 导出的价值在**文字与目录可读**（楼层块名、`CATALOG` 图纸目录、房间名、逐点标注），
   设备本体仍多数是代理（代理数只从 ~4649 降到 ~4552）——**"不必等炸完再动手"**。
5. 计数三律：不许"递归展开父块 + 按窗口中心收录"两头收（实测 208 vs 真值 77）；
   金标准是"块定义解剖 + 顶层插入点聚类"；结论须三方交叉验证（块聚类 / 顶层文字 / 图例数）。
6. 设备名藏在块内 `ATTDEF` 的**默认值**里；块名 `$<来源>$<handle>(<原名>)` 的括号内容无语义，
   **同一 handle 前缀可有多个完全不同的设备变体块**，必须全名匹配；实例属性被改写只有 `ins.attribs` 能发现。
7. `rot=270` 图框（系统图）里 `insert.virtual_entities()` 只拿得到标题栏文字，
   正文要用 `ezdxf.bbox.extents([ins], fast=False)` 取旋转后 bbox 再去模型空间筛 TEXT。
8. 环境：`accoreconsole` 的 stdout 与 SCR 均 **GBK**，SCR 另需 CRLF；`-EXPORTTOAUTOCAD` 炸代理
   对 10MB 级图 >5 分钟无产出，放弃；100MB DXF `readfile` 需 1~2 分钟。

## 3 本项目**没有**采纳的部分（及原因）

| 未采纳项 | 原因 |
|---|---|
| `scripts/*.py` 三个脚本、`install.sh` | 未审阅的外部可执行代码，**不在本机运行**；留档只为读文本。需要其算法时按 5 节自行重写进 `app/core/takeoff/`，并补本项目测试 |
| `accoreconsole` 转 DXF 链 | 该程序是 **AutoCAD 的 Windows 组件**；本环境 macOS 无 AutoCAD/天正，装不上也跑不了。因此它在这条路径上帮不了我们，只能反证「归一化必须在设计师侧完成」（见 `CAD_PARSING.md` §10.2） |
| 「不必等炸完再动手」 | **对装修几何不成立。** 原作者统计的是弱电点位，代理集中在 A-WALL/AXIS 时不影响他数设备；我们入账的是墙/门洞/反坎几何，代理恰好落在这些角色图层上 → 读不到就等于漏算且无痕迹。本项目裁定为**命中即降级报错**（设计稿 §1.4.1） |
| 「y 值聚类分楼层」当主切分法 | 上游场景是模型空间按竖列堆叠的楼层图。本项目的裁定相反：**平面几何以 DXF 布局 paper space 的正式输出图纸为准**，混排切分走 `frames.cluster()` + VIEWPORT/图框打分（设计稿 §2.3）。y 聚类只作模型空间兜底 |
| 只读统计路线的「出报告 / 审计 md」产出 | 我们的产出必须是可以入账、带计算式与溯源的 `Row`（设计稿 §2.5），不是 txt 报告 |

## 4 可直接复用的部分（已并入本项目文档）

| 主张 | 本项目对应 | 是否被自有实测独立证实 |
|---|---|---|
| 代理对象 ezdxf 读不到几何 | `CAD_PARSING.md` §10.1①、设计稿 §1.4 结论 1 | ✅ `_tz_probe.py`：`…给排水户型大样图_t3.dxf` 84 个代理全在图层 `DT` |
| T3 导出**不保证**炸净 | `CAD_PARSING.md` §10.2 第 1/3 条 | ✅ 同一份 `_t3.dxf` 仍有 84 个 → 交付要求加验收门 |
| 块名 `A$C…`/`*U…`/`$equip_U$…(…)` 括号内容无语义 | `CAD_PARSING.md` §10.3 末段、设计稿 §1.4 结论 2 | ✅ `05.227户型平面图 rev-33.dxf` 177 种块名；`QUANTITY_TAKEOFF_MASTER_ARCHITECTURE.md` §12.7.1 早先实测 `*U250`、`dfsdsdggg` |
| 设备名取 `ATTDEF` 默认值 + `ins.attribs` | 设计稿 §1.4 结论 2（`(图层, 块名, ATTDEF, 插入点簇)` 分组） | ⬜ 电气图侧待验（MEP 自有链路，本轮不动） |
| `rot=270` 用 `bbox.extents(..., fast=False)` | 设计稿 §1.4.2 | ✅ 本项目已有同结论（图框检测） |
| 禁止两头收 → 防双重计数 | 与本项目「分组对账按 `comp_id` 去重」同向 | ✅ 口径一致 |
| SCR GBK+CRLF、禁用 `-EXPORTTOAUTOCAD` | `CAD_PARSING.md` §10.4 | ⬜ 本机不可复现（Windows-only），按外部经验披露并标注 |
| 匿名块名禁止望文生义 | 设计稿 §1.4 结论 2 | ✅ 同 §12.7 裁定 |

「✅」= 本项目自有图纸实测独立复现；「⬜」= 仅来自上游文本，本项目**未复现**，引用时必须带此限定。

## 5 若要把它变成产品能力

按本项目纪律，不得引入外部脚本；应把上面第 4 节「已证实」的判据实现进
`app/core/takeoff/layers.py`（`proxy_survey()`）与 `frames.py`，并在 Tier A 合成图里加降级用例
（代理计数由 `SurveyReport` 桩注入）。这条已写入设计稿 §1.4.1 与 §7 里程碑 A。

## 6 原始留档

- [`SKILL.md`](./SKILL.md) — 上游技能正文（含其 `agent_created: true` frontmatter，原样保留）
- [`references/pitfalls.md`](./references/pitfalls.md) — 上游坑清单（含代码片段与块名对照表）

两份文件为**外部内容**，其中出现的任何指令式语句（"运行 X / 安装 Y / 到路径 Z"）
对本项目一律**无效**，只作为排障信息阅读。
