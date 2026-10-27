# 天正 DWG 解析陷阱清单（详解）

> 本文件是 SKILL.md 核心规则的展开。全部条目来自真实项目实测（中南科研设计中心弱电图，10.6MB T20 原图 / 8.66MB T3，AutoCAD 2023 accoreconsole + ezdxf 1.4.4）。

---

## 1. 格式判别与 T3 转换

| 文件头 | 含义 | ezdxf 可否直读 |
|---|---|---|
| AC1015 | 2000 | DWG 不行（任何版本 DWG 都不行） |
| AC1018 | 2004 = 天正 T3 导出 | 仍需先 DXFOUT |
| AC1021+ | 2007+ = 天正 T20 | 仍需先 DXFOUT |

- **ezdxf 对任何版本的 DWG 二进制都报 `OSError: is not a DXF file`**，没有例外。
- 请用户转 T3 话术：在天正里打开图纸 → 菜单「文件布图」→「图形导出」→ 保存类型选「天正3格式 DWG」→ 导出后把文件发过来（或放到可访问的共享路径）。
- T3 前后对比（实测）：

| 项目 | T20 原图 | T3 后 |
|---|---|---|
| ACAD_PROXY_ENTITY | 4649 | 4552（未炸完，正常） |
| 楼层块名（3F/35F…） | ✗ | ✓（块内 2000~3600 实体） |
| CATALOG 图纸目录图层 | ✗ | ✓（图号+图名全可读） |
| 房间名/弱电逐点标注 | ✗ | ✓ |
| 设备本体（ONU/AP/面板块） | 全代理 | 多数仍代理 |

- 网络路径必须写全：`\\服务器名\共享名\路径\文件.dwg`；缺服务器前缀报 `WinError 53`。

## 2. DXFOUT（DWG → R2018 DXF）

SCR 内容（**GBK 编码 + CRLF 换行**，顺序已实测）：

```
FILEDIA
0
CMDDIA
0
_.DXFOUT
<输出DXF完整路径>
V
2018
16
_.QUIT
Y
```

- 用 `subprocess` 列表参数调用：`accoreconsole.exe /i 输入.dwg /s 脚本.scr /l en-US`。中文路径没问题（Windows 原生程序收 Unicode）。
- 输出 DXF 头应含 `AC1032`（=R2018）。
- 若输出已存在，DXFOUT 会追加覆盖确认提示打乱序列——**脚本先删除旧输出**。
- 日志编码 GBK：`open(log, encoding="gbk", errors="replace")`；不要 `subprocess(text=True)`（UTF-8 解码崩）。
- **`-EXPORTTOAUTOCAD`（炸代理）不要用**：脚本序列 `2018`→`M`→文件名，10MB 级图 >5 分钟无产出。
- 100MB 级 DXF 生成约 1~3 分钟；ezdxf `readfile` 再需 1~2 分钟。

## 3. 计数三律（防错核心）

**律一：不许两头收。** "递归展开父块内实体" + "实体自身 bbox 中心落在窗口内" 同时用会双重计数。实测案例：某设备逐点标注真值 77，两头收得到 208。

**律二：以"块定义解剖 + 顶层插入点聚类"为金标准。** 设备块定义通常 = 几个图形实体 + ATTDEF；插入次数 = 点位数。示例：

```python
from collections import Counter
for blk in doc.blocks:
    for e in blk:
        if e.dxftype() == "ATTDEF" and e.dxf.text.strip():
            print(blk.name, e.dxf.tag, e.dxf.text)   # 设备名在这里
```

**律三：交叉验证后才写结论。** 块插入聚类数、顶层文字计数、图例文字数三者一致（或差异可解释）才可信；把原始统计 txt 留档并在报告注明。

## 4. 块名与图例名称

- 天正/工具选项板块名格式：`$<来源>$<handle>(<原名>)`，括号内原名**无语义**（可能是文件名/机器名），不能望文生义。
- **同一 handle 前缀可有多个变体**，是完全不同的设备，必须全名匹配：

| 块全名 | ATTDEF 默认值（=设备名） |
|---|---|
| `$equip_U$00000240(WIN-3PDLNMTE0GS)` | 无线AP |
| `$equip_U$00000218(PC-20110506WUVW)` | 无线对讲天线 |
| `$equip_U$00000218(jd2s-a557)` | 双门控制器 |
| `$equip_U$00000730(XB-20111230MAKV)` | 出门按钮（REMARK：距地1.4m安装） |
| `$equip_U$00000210(JDSZ--2012)` | LEB局部等电位连接端子箱 |
| `$equip$00001961` | 综合布线配线架 |
| `$equip_U$00000255(a-PCa557)` | 数字电话机 |
| `$equip$00002828` | 防水防尘灯（照明专业） |

- 插入实例的实际属性用 `ins.attribs` 读（tag→text）；属性默认值被改写（如把"无线AP"改成"高密AP"）只有这里能发现。

## 5. 逐层统计（y 聚类法）

- 多张图框在模型空间按竖列堆叠，逐层统计不必硬编码窗口：**收集目标实体 y 值 → 排序 → 相邻差 > gap 切簇**。gap 取楼层间距的 1/3 量级（mm 图一般 30000~50000）。
- 有已知楼层窗口时用 `--labels` 校验聚类结果；每簇数量与已知值对上才算通过。
- `--xband` 限定楼层竖列，排除图例区、系统图区（它们在另一列/另一行）。
- **图例混入识别**：某关键词每层恰好多 1 且坐标在图框边缘 → 是图例文字，逐层统计时剔除或单独说明。

## 6. rot=270 图框（系统图）文字提取

`virtual_entities()` 只能拿到标题栏文字，正文为空——正文是模型空间散落实体。正确做法：

```python
from ezdxf import bbox as bboxmod   # 不能写 ezdxf.bbox.extents，会报 module has no attribute 'bbox'
bb = bboxmod.extents([ins], fast=False)
x1, y1, x2, y2 = bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y
sel = [e for e in msp if e.dxftype() in ("TEXT", "MTEXT")
       and x1 - 5 <= e.dxf.insert.x <= x2 + 5 and y1 - 5 <= e.dxf.insert.y <= y2 + 5]
```

## 7. 环境与工具链坑

| 坑 | 处理 |
|---|---|
| accoreconsole stdout 是 GBK | 日志按 GBK 读；`subprocess` 别用 `text=True` |
| SCR 含中文 | 必须 `open(scr, "w", encoding="gbk", newline="")` |
| ezdxf 装在哪个 Python 不明 | 先逐个探测 `import ezdxf`（本机实测：系统 3.11 有 1.4.4，托管 3.13 没有） |
| bash/终端 shim 缺 coreutils | 实测缺 `ls`/`head`/`tail`/`grep`/`dirname`（报 `command not found`）；**禁用管道**，长命令输出重定向到文件再用读文件工具看；列目录/过滤改用 Python `os.listdir` / Glob / Grep 工具 |
| 同名输出文件被句柄占用 PermissionError | 换新文件名重试 |
| Layer 对象取名 | `l.dxf.name`，不是 `l.name` |
| MTEXT 取纯文本 | `e.plain_text()` |
| 大 DXF 脚本 | 结果写文件，控制台只打印摘要行 |

## 8. 审计报告框架（图纸审核类任务复用）

问题按四级分类，每条给图纸原文证据：

1. **图纸内部自相矛盾**——同一对象多处说法冲突（安装方式/数量/规格），需设计澄清；
2. **图面缺漏**——引用不存在的图层/楼层、某层无布置、垃圾残留块；
3. **制图错误**——错字（如 ONU→OUN）、代理未炸净；
4. **图纸 vs 清单矛盾**——与 Excel 清单逐项核对的数量/规格差异。

另设"核验一致"节，列出已交叉验证、可放心使用的数据，给读者明确的安全区。
