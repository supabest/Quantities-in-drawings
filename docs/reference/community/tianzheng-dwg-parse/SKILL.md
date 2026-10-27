---
name: tianzheng-dwg-parse
description: 解析天正(T20/T8)及 T3 导出的 DWG 施工图：accoreconsole 转 DXF 后用 ezdxf 做全图普查、图纸目录/图签/旋转图框破译、逐层聚类统计设备点位、块名与图例名称映射、块属性核查。当用户提供弱电/机电/智能化等天正 DWG，要求查看图纸、统计设备数量、审核图面问题或与 Excel 清单核对时使用。产出统计报告与审计素材文件。
agent_created: true
---

# 天正 DWG 图纸解析（只读统计路线）

> 天正自定义对象（ACAD_PROXY_ENTITY）让 ezdxf 完全读不到内容。本技能是实测走通的解析链路：**T3 导出 → accoreconsole DXFOUT → ezdxf 解析 → 逐层统计**。往图纸写标记、写回 DWG、批量出图 PDF 用 `autocad-dwg-headless` 技能，两者互补不重叠。

## 工作流（5 步）

### 1. 判格式，必要时请用户转 T3

- 读 DWG 文件头 6 字节：`AC1018` = 2004/T3，`AC1021` 及以上 = 2007+/T20。
- T20 原图 → 请用户在天正执行「**文件布图 → 图形导出 → 天正3格式(.dwg)**」。T3 的价值：楼层块名（`3F`/`35F`…）、`CATALOG` 图纸目录、房间名、弱电逐点标注文字全部可读；设备本体仍可能是代理（代理数只从 ~4649 降到 ~4552，**不必等炸完再动手**）。
- 代理对象集中在建筑图层（A-WALL/WINDOW/AXIS/尺寸标注），弱电文字与标注层基本干净，不影响统计与审计。

### 2. DWG → R2018 DXF

```bash
python scripts/dxfout.py 输入.dwg -o 输出.dxf --acad "accoreconsole.exe完整路径"
# 不确定 accoreconsole 位置时先 --dry-run 看 SCR
```

- ezdxf 不能读 DWG 二进制（报 `is not a DXF file`），必须先转 DXF。
- 内部用 `_.DXFOUT`（SCR 必须 **GBK+CRLF**，参数序列 `FILEDIA 0 / CMDDIA 0 / _.DXFOUT / 路径 / V / 2018 / 16 / _.QUIT / Y`）。
- **禁用 `-EXPORTTOAUTOCAD`**：对 10MB 级天正图 >5 分钟无产出，实测放弃。

### 3. 全图普查

```bash
python scripts/tz_survey.py 输出.dxf -o survey.txt --keywords 无线AP,ONU,支架,托盘,光分路器,配线架,机柜,门禁
```

产出：实体类型统计、代理对象按图层分布、图层清单、顶层块引用计数（TOP N）、按图层文字清单（去重计数）、关键词命中明细。**从普查结果定位：图纸目录图层、图例图层、楼层块名与窗口坐标、设备块名清单。**

### 4. 逐层统计 + 块属性核查

```bash
python scripts/tz_floors.py 输出.dxf --block 00000240 --xband 300000,470000 --gap 40000 \
    --labels 3F=156395-240495,35F=895137-979237
```

- `--block 块名片段`（可多次）：按 y 聚类插入点 → 逐簇数量 + ATTRIB 实际值分布（判断属性是否被改写）。
- `--text 文字片段`（可多次）：顶层文字逐簇计数——T3 下逐点标注往往比设备本体更可靠（设备本体可能是代理）。
- `--labels`：已知楼层窗口做校验；未知楼层靠聚类自动分簇（`--gap` 取楼层间距的 1/3 量级，图面单位通常 mm）。
- `--xband x0,x1`：限定楼层图纸竖列，排除图例区/系统图区干扰。

### 5. 出报告

- 每个数字落到证据文件（`*.txt`），报告里注明来源文件；**交叉验证**（块插入聚类 vs 顶层文字计数 vs 图例数）三方一致才写入结论。
- 典型产出：普查 txt、逐层统计 txt、审计 md（问题按四级分类：图纸内部矛盾 / 图面缺漏 / 制图错误 / 图纸-清单矛盾）。

## 核心规则（违者必错，详解与代码见 references/pitfalls.md）

1. **计数防双重统计**：不要"递归父块展开 + 按窗口中心收录"两头同时收——同一实体会被计两次（实测支架 208 vs 真值 77）。以顶层文字计数或"块定义解剖 + 插入点聚类"为准。
2. **设备名藏在 ATTDEF 默认值里**：天正设备块 = 符号图形 + ATTDEF（tag=`A` 的默认值即设备名，如「无线AP」）。块名 `$equip_U$00000240(WIN-3PDLNMTE0GS)` 的括号内容无语义，须全名匹配；**同 handle 前缀可能有多个变体块**（如 00000218 同时存在"无线对讲天线"与"双门控制器"两个不同设备），必须用完整块名区分。
3. **rot=270 图框**（系统图类）：`insert.virtual_entities()` 只能拿到标题栏文字；用 `from ezdxf import bbox as bboxmod; bboxmod.extents([insert], fast=False)` 取旋转后精确 bbox，再筛模型空间 TEXT/MTEXT，正文即可完整提取。
4. **环境**：accoreconsole 的 stdout 与 SCR 均为 GBK；100MB 级 DXF `readfile` 需 1~2 分钟，统计结果写文件、不要全量打印；网络路径要写全 `\\server\share\...`（缺前缀报 WinError 53）；先探测哪个 Python 装了 ezdxf 再跑脚本。

## references/pitfalls.md

完整坑清单：DXFOUT 失败排查、双重计数案例数字、块名变体对照表、rot270 提取代码、y 聚类参数选择、图例文字混入统计的识别、常见报错对照表、请用户转 T3 的话术模板。
