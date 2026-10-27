# CAD/DXF/PDF 解析技术选型方案

> **文档版本**: v1.1
> **日期**: 2026-08-09（v1.0）／2026-10-18（v1.1 追加第十节「图纸交付要求（天正图归一化）」）
> **决策状态**: 待确认（第一~九节为技术选型）；第十节为**收图硬前提，已生效**

---

## 一、总体策略

```
DWG (原始CAD)
  ├──→ [libredwg/ODA] → DXF
  ├──→ [ezdxf] → 结构化几何数据 (LINE/POLYLINE/ARC/HATCH/TEXT...)
  └──→ [CAD→PDF]  → 视觉分析 (PyMuPDF/pdfplumber)
                          ↓
PDF (施工图PDF) ─────→ [PyMuPDF+OCR] → 语义数据 (材料/标注/文字)
                          ↓
                    DrawingUnifiedModel
                          ↓
                    CAD几何 + PDF语义 融合
```

**核心原则：CAD 负责精确几何，PDF 负责语义信息，两者独立解析后融合。**

---

## 二、DWG 解析

### 2.1 方案对比

| 方案 | 成熟度 | macOS兼容 | 许可 | 能力 | 推荐 |
|------|--------|-----------|------|------|------|
| **libredwg + ezdxf** | ⭐⭐⭐ | ✅ (brew) | GPLv3 | DWG→DXF转换，读取全部实体 | 🏆 **首选** |
| **ODA Drawings SDK** | ⭐⭐⭐⭐⭐ | ✅ | 商业许可 | 完整DWG读写，最准确 | 💰 商业备选 |
| **QCAD CLI** | ⭐⭐⭐⭐ | ✅ | 开源/商业 | DWG→DXF/PDF 批量转换 | ✅ **备选** |
| **AutoCAD OEM/CLI** | ⭐⭐⭐⭐⭐ | ❌ | 商业 | 最强但需Windows + 许可 | ❌ 不可行 |
| **ODA File Converter (Docker)** | ⭐⭐⭐⭐ | ✅(容器) | 免费评估 | 批量DWG→DXF/PDF | 🔧 **Docker方案** |

### 2.2 推荐方案：libredwg + ezdxf

```bash
# macOS 安装
brew install libredwg

# Python 库
pip install ezdxf
pip install libredwg  # Python bindings (如果可用)
```

**libredwg CLI 使用：**
```bash
# DWG → DXF 转换
dwg2dxf input.dwg -o output.dxf

# 查看 DWG 信息（版本、图层、实体数量）
dwgread -v4 input.dwg

# DWG → JSON（结构化数据）
dwgread -O json input.dwg
```

**局限性：**
- libredwg 对某些 DWG 2018+ 版本支持不完整
- 复杂 3D 实体、动态块支持有限
- GPLv3 许可（使用时需注意合规）

### 2.3 Docker 后备方案：ODA File Converter

当 libredwg 无法解析的 DWG 文件，使用 Docker 容器运行 ODA：

```dockerfile
# cad-converter/Dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y wget libgl1 libx11-6
RUN wget https://download.opendesign.com/.../ODAFileConverter_AppImage -O /usr/local/bin/oda
RUN chmod +x /usr/local/bin/oda
WORKDIR /data
ENTRYPOINT ["/usr/local/bin/oda"]
```

```bash
# 批量转换
docker run --rm -v $(pwd):/data cad-converter \
  ODAFileConverter /data /data ACAD2018 DXF 0 1
```

### 2.4 DWG 解析策略决策树

```
DWG 文件
    │
    ├── 尝试 libredwg dwg2dxf
    │   ├── ✅ 成功 → ezdxf 解析 DXF
    │   └── ❌ 失败
    │       ├── 尝试 QCAD CLI
    │       │   ├── ✅ → ezdxf 解析输出
    │       │   └── ❌ → Docker ODA 方案
    │       └── 仍失败 → 标记为 "DWG 解析失败，需手动转换"
    │
    └── 并行：所有 DWG → PDF 转换（用于视觉分析）
```

---

## 三、DXF 解析（核心几何数据源）

### 3.1 推荐：ezdxf

```bash
pip install ezdxf
```

### 3.2 提取实体清单

```python
import ezdxf

doc = ezdxf.readfile("floor_plan.dxf")
msp = doc.modelspace()

# 按实体类型提取
entities = {
    'lines': [],        # LINE
    'polylines': [],    # POLYLINE
    'lwpolylines': [],  # LWPOLYLINE — 最常用于房间/区域边界
    'arcs': [],         # ARC
    'circles': [],      # CIRCLE
    'hatches': [],      # HATCH — 材质填充区域
    'inserts': [],      # INSERT — 图块引用（门/窗/家具符号）
    'dimensions': [],   # DIMENSION — 尺寸标注
    'texts': [],        # TEXT
    'mtexts': [],       # MTEXT — 多行文字（房间名/材料说明）
    'blocks': [],       # BLOCK 定义
    'layers': set(),    # 图层名
}

# 迭代所有实体
for entity in msp:
    dxftype = entity.dxftype()
    layer = entity.dxf.layer
    entities['layers'].add(layer)

    if dxftype == 'LINE':
        entities['lines'].append({
            'start': (entity.dxf.start.x, entity.dxf.start.y),
            'end': (entity.dxf.end.x, entity.dxf.end.y),
            'layer': layer,
            'handle': entity.dxf.handle,
        })
    elif dxftype == 'LWPOLYLINE':
        points = [(p[0], p[1]) for p in entity.get_points()]
        is_closed = entity.closed
        entities['lwpolylines'].append({
            'points': points,
            'closed': is_closed,
            'area': _calculate_polygon_area(points) if is_closed else 0,
            'layer': layer,
            'handle': entity.dxf.handle,
        })
    elif dxftype == 'HATCH':
        # HATCH 路径 → 材质区域 Polygon
        for path in entity.paths:
            path_type = path.path_type  # 'polyline' or 'edge'
            entities['hatches'].append({
                'path_type': path_type,
                'vertices': path.vertices,
                'layer': layer,
                'handle': entity.dxf.handle,
                'pattern_name': entity.dxf.pattern_name,  # 填充名称→推断材质
            })
    elif dxftype == 'TEXT' or dxftype == 'MTEXT':
        entities['texts'].append({
            'text': entity.dxf.text if dxftype == 'TEXT' else entity.text,
            'position': (entity.dxf.insert.x, entity.dxf.insert.y),
            'layer': layer,
            'handle': entity.dxf.handle,
        })
    elif dxftype == 'INSERT':
        entities['inserts'].append({
            'block_name': entity.dxf.name,
            'position': (entity.dxf.insert.x, entity.dxf.insert.y),
            'scale': (entity.dxf.xscale, entity.dxf.yscale),
            'rotation': entity.dxf.rotation,
            'layer': layer,
        })
    elif dxftype == 'DIMENSION':
        entities['dimensions'].append({
            'text': entity.dxf.text if hasattr(entity.dxf, 'text') else '',
            'measurement': entity.dxf.dim_measurement,
            'layer': layer,
        })
```

### 3.3 关键应用场景

| DXF 实体 | 算量用途 |
|----------|---------|
| LWPOLYLINE (闭合) | 房间边界、材料区域 Polygon → 面积计算 |
| LINE | 墙体线段 → 长度累加 |
| HATCH | 填充区域 → 材质分区边界 |
| INSERT (block) | 门/窗/家具符号 → 定位 + 计数 |
| DIMENSION | 尺寸标注 → 比例尺标定参考 |
| TEXT/MTEXT | 房间名称、材料编号、设计说明 |
| LAYER | 图层分离（墙层/门窗层/填充层/标注层） |

> ⚠ 上表的前提是**图层里的实体是原生 AutoCAD 实体**。天正图的墙/窗/标注常是
> `ACAD_PROXY_ENTITY`（自定义对象），ezdxf 遍历得到类型名但**取不到任何几何**，
> 于是「按图层求面积/计数」会返回 0 而不是报错。天正图的收图门见第十节。

---

## 四、PDF 解析（语义数据源）

### 4.1 推荐技术栈

```bash
pip install PyMuPDF          # PDF 页面解析 + 图片提取
pip install pdfplumber       # 表格 + 文字（备选）
pip install pdf2image        # PDF → 图片（用于 OCR）
pip install pytesseract      # OCR 引擎
brew install tesseract       # macOS Tesseract
brew install tesseract-lang  # 中文语言包
```

### 4.2 解析流程

```python
import fitz  # PyMuPDF

doc = fitz.open("construction_drawing.pdf")

pages_data = []
for page_num, page in enumerate(doc):
    page_data = {
        'page_number': page_num + 1,
        'width': page.rect.width,
        'height': page.rect.height,
        'text_blocks': [],
        'images': [],
        'tables': [],
        'drawings': [],  # 矢量图形
    }

    # 1. 提取文字块（含位置）
    blocks = page.get_text("dict")["blocks"]
    for block in blocks:
        if block["type"] == 0:  # 文字块
            for line in block["lines"]:
                text = "".join([span["text"] for span in line["spans"]])
                bbox = line["bbox"]
                page_data['text_blocks'].append({
                    'text': text,
                    'bbox': bbox,
                    'font_size': line["spans"][0]["size"] if line["spans"] else 0,
                })

    # 2. 提取图片（用于 OCR 或直接分析）
    image_list = page.get_images(full=True)
    for img_index, img in enumerate(image_list):
        xref = img[0]
        base_image = doc.extract_image(xref)
        page_data['images'].append({
            'xref': xref,
            'size': (base_image["width"], base_image["height"]),
            'bbox': page.get_image_bbox(img),
        })

    # 3. 提取矢量图形（线条/矩形）
    drawings = page.get_drawings()
    for draw in drawings:
        page_data['drawings'].append({
            'type': 'path' if draw.get("items") else 'rect',
            'rect': draw["rect"],
            'fill': draw.get("fill"),
            'color': draw.get("color"),
        })

    pages_data.append(page_data)
```

### 4.3 PDF 信息提取重点

| 提取目标 | 方法 | 用途 |
|---------|------|------|
| 材料编号 (ST-01, CT-02...) | 正则匹配文字 | 材料→区域关联 |
| 房间名称 | 大字体文字块 + 位置 | 空间命名 |
| 尺寸标注 | 数字+单位模式 附近 | 比例尺标定 |
| 图例表 | 特定区域文字聚类 | 符号解读 |
| 设计说明 | 段落文字 | 做法解读 |
| 填充图案 | 矢量图形分析 | 材质分区边界 |
| 门/窗符号 | 图片 OCR + 位置 | 构件识别 |
| 页码/图纸编号 | 页眉/页脚文字 | 图纸组织 |

### 4.4 OCR 增强（对于扫描版 PDF）

```python
import pytesseract
from pdf2image import convert_from_path

# PDF → 图片
images = convert_from_path('scanned_drawing.pdf', dpi=300)

for i, image in enumerate(images):
    # OCR with Chinese support
    text = pytesseract.image_to_string(image, lang='chi_sim+eng')
    # 获取文字位置
    data = pytesseract.image_to_data(image, lang='chi_sim+eng',
                                      output_type=pytesseract.Output.DICT)
    # data 包含每个词的 bbox、confidence
```

---

## 五、CAD 命令列工具

### 5.1 可选工具

| 工具 | 用途 | 安装 |
|------|------|------|
| QCAD Pro CLI | DWG→DXF/PDF 批量转换 | macOS/Linux/Windows |
| LibreCAD | DXF 查看/编辑 | 开源 |
| ODA Drawings Explorer | DWG 结构浏览 | ODA 会员 |

### 5.2 QCAD CLI 示例

```bash
# DWG → DXF
qcad -no-gui -autostop scripts/convert_dwg_to_dxf.js input.dwg

# DWG → PDF
qcad -no-gui -autostop scripts/convert_dwg_to_pdf.js input.dwg
```

---

## 六、容器化 CAD 解析方案

### 6.1 Docker Compose 扩展

```yaml
# 在现有 docker-compose.yml 基础上增加
services:
  cad-parser:
    image: cad-parser:latest
    build:
      context: ./docker/cad-parser
    volumes:
      - ./uploads:/data/uploads:ro
      - ./parsed:/data/parsed
    environment:
      - PARSER_MODE=auto  # libredwg | oda | qcad | auto
    deploy:
      resources:
        limits:
          memory: 2G

  web:
    # ... 现有配置
    volumes:
      - ./parsed:/app/parsed  # 挂载解析结果
```

### 6.2 容器内工具链

```dockerfile
# docker/cad-parser/Dockerfile
FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    libredwg-tools \
    qcad \
    python3 python3-pip \
    poppler-utils \
    tesseract-ocr tesseract-ocr-chi-sim

RUN pip3 install ezdxf PyMuPDF pdfplumber pdf2image pytesseract

COPY parse_worker.py /app/
WORKDIR /app
CMD ["python3", "parse_worker.py"]
```

---

## 七、方案决策

### 推荐技术栈（优先级排序）

| 层级 | 技术 | 说明 |
|------|------|------|
| DWG→DXF | libredwg (brew) | 首选，开源免费 |
| DWG→DXF Fallback | ODA File Converter (Docker) | 后备方案 |
| DXF 解析 | ezdxf | Python 原生，最成熟 |
| PDF 解析 | PyMuPDF (fitz) | 速度最快，功能最全 |
| PDF 表格 | pdfplumber | 补充表格提取 |
| PDF OCR | Tesseract + chi_sim | 扫描版图纸 |
| DWG→PDF | QCAD CLI | 批量转换 |
| 图片处理 | OpenCV (已有) | 标注/可视化 |
| 容器化 | Docker + docker-compose | CAD 工具环境隔离 |

### 建议的依赖扩展

```txt
# 新增到 requirements.txt
ezdxf>=1.3.0
PyMuPDF>=1.24.0
pdfplumber>=0.11.0
pdf2image>=1.17.0
pytesseract>=0.3.10
shapely>=2.0.0          # 几何计算（Polygon 面积/叠加/缓冲）
celery>=5.4.0           # 异步任务队列
redis>=5.0.0            # Celery broker + 结果缓存
structlog>=24.1.0       # 结构化日志
```

---

## 八、实施计划

### Phase 1a：CAD 解析基础（第 1-2 周）
1. 安装 libredwg + ezdxf + PyMuPDF
2. 实现 `CADServices` 类（DWG→DXF, DXF→结构化数据）
3. 实现 `PDFServices` 类（PDF→文字块/图片/矢量）
4. 在现有项目中集成（不破坏现有 OpenCV 流程）

### Phase 1b：测试验证（第 2-3 周）
1. 收集真实装修 CAD 图纸样本
2. 验证 DXF 实体提取完整性
3. 验证 PDF 文字提取准确性
4. 建立测试数据集

### Phase 1c：Docker 方案（第 3-4 周）
1. 构建 cad-parser Docker 镜像
2. 集成 ODA File Converter
3. 端到端测试 DWG→数据 完整流程

---

## 九、多源融合与图纸语义识别规则

> 本节为 Phase 2（融合引擎）与 Phase 3（空间理解）的实现规则，
> 吸收自《AI工程量计算读图算量注意事项》v1.0 §1/§5.1 与多格式验证架构讨论。
> 对应知识库条目「多格式图纸权威域分工与融合去重」。

### 9.1 多格式权威域分工

同一图纸存在多种格式时，各格式的数据权威域如下，**禁止跨域取数**：

| 格式 | 权威域 | 不得作为 |
|------|--------|---------|
| DXF / DWG | 几何坐标、面积、周长、HATCH 图案名、图层名、块名（INSERT） | — |
| 矢量 PDF（CAD 导出） | 文字语义：房间名、材料编号、尺寸标注、材料表、设计说明 | 几何权威（图层信息已丢失） |
| 扫描版 PDF / 光栅图片 | 兜底识别、标注渲染 | 尺寸权威源（须 OCR，误差大） |

- 矢量 PDF 的文字是真实文本对象（PyMuPDF 零误差提取），**文字语义一律以 PDF/DXF TEXT 实体为准，禁止对矢量内容做 OCR**。
- 扫描版 PDF 等同光栅图片处理，先 OCR 再降级为"经验补全"类置信度。

### 9.2 融合去重规则

同一实体在多格式中重复出现（如一面墙既是 DXF 几何实体又有 PDF 文字标注），归并为一条记录：

1. **身份键**：优先 CAD handle；无 handle 时用 bbox 重叠度（IoU > 0.8 视为同一实体）
2. **多来源记录**：归并后保留全部 source_file / source_page / source_bbox，写入 DrawingSource 列表
3. **只出一条清单项**：融合层去重后进入算量引擎；审计层再查"同一区域同一材料出现两次"（KPI 重复计算率 <2%）

### 9.3 冲突仲裁规则

| 冲突类型 | 仲裁规则 |
|---------|---------|
| 几何形状 vs 文字语义 | 几何定形（DXF 定边界），文字定义（PDF 定材质/名称） |
| DXF 实测尺寸 vs 标注尺寸 | 以标注尺寸为准，偏差 >5% 输出审计提示 |
| 平面图 vs 立面图宽度不符 | 以平面为准，备注差异 |
| 矢量几何 vs HATCH 填充面积 | HATCH 只作分界，面积按标注尺寸重算（勿直读填充面积） |

### 9.4 立面饰面分界线识别（PDF 矢量/DXF 图层）

同一立面多种材质的分界判别优先级：**填充边界 > 粗实线 > 文字引线 > 细实线/虚线**。

| 分界表达 | 处理 |
|---------|------|
| CAD HATCH 边框 | 该材质覆盖范围（结合 pattern_name 推断材质） |
| 粗实线 | 材质交界，沿此线分割面积 |
| 文字引线 | 引线起点定位分界，所指区域为该材质 |
| 细实线/虚线 | 辅助定位线或构造缝，不作为分区依据 |

- 同一立面三种以上材质时逐区列项（北墙—木饰面区、北墙—乳胶漆区），不得合并。
- 分界线冲突时，以平面图尺寸为最终校核依据。

### 9.5 天花图线型识别

- **实线 = 物理边界**（跌级边缘、灯槽外框、窗帘盒外框、吊顶分区线）→ 算量依据
- **虚线 = 灯带位置示意** → 不作为面积或长度的算量依据
- 跌级天花：平面只取轮廓范围，级数高差从剖面/大样读取
- 天花线 vs 墙线四步判别：① 位置（造型线在墙线以内）→ ② 图层（ceiling/wall，PDF 丢失图层时跳过）→ ③ 线宽（墙线更粗）→ ④ 仍无法确认 → 备注歧义、按保守处理、🔴低
- 灯槽/窗帘盒计量方式（单独计米 vs 综入天花）须先与用户确认，见知识库条目「灯槽/窗帘盒计量须先与用户确认」

---

## 十、图纸交付要求（天正图归一化 · 收图硬前提）

> **版本**：v1.1 追加（2026-10-18）。
> **适用范围**：所有由天正建筑 TArch / T20 / T8 绘制后导出的 DWG/DXF。本节是**对设计师的交付要求**，
> 不是解析器的容错策略——命中即退回重导，应用侧绝不试图绕过。
> 对应用侧的行为定义见 `docs/superpowers/specs/2026-10-17-general-takeoff-engine-design.md` §1.4。

### 10.1 三个硬伤（实测确认，非纸面推断）

探针脚本：仓库根 `_tz_probe.py`（临时件，不入库）。对象：`装修图纸/` 下 20+ DXF、24 DWG。

| 硬伤 | 表现 | 实测数据 |
|---|---|---|
| **① 图层被天正匿名化 / 代理对象读不到** | `ACAD_PROXY_ENTITY` 是 AutoCAD 自定义对象，ezdxf 只看到类型名，**拿不到任何几何**；被代理的恰是墙、窗、轴网、标注 | `…给排水户型大样图_t3.dxf`：**已做过天正 3 导出，仍剩 84 个代理，全部在图层 `DT`**；块名 `$TwtSys$00000125`×42 |
| **② 块名乱码** | 块名由天正/工具选项板自动生成，不携带语义 | `05.227户型平面图 rev-33.dxf`：177 种块名 = `A$C…` 50 / `*U…` 43 / 其他 ASCII 83，TOP `A$C64342E2E`×106；电气图 125 种 `*U` + 143 种 `A$C` + `$TD_AUDIT_GENERATED_(80982)`×57 |
| **③ 多楼层/多平面图元混排** | 同一文件叠放多套平面复制，不按图框/视口切分就会跨复制重复计数 | 同文件：平面分布在 布局1 的 **18 个 VIEWPORT** 与 `A$C5D74690A`×42 图纸块内；电气图为 136 与 227 **两户型同文件** |

代码页统一为 `ANSI_936`(GBK)，中文另以 `\M+5HHHH` 转义存储（见记忆条目「AutoCAD 原生 DXF 中文以 \M+5HHHH 形式 GBK 转义存储需解码」）。

### 10.2 交付要求（设计师侧，缺一不收图）

1. **天正图形必须导出为天正 3 格式**：天正里「**文件布图 → 图形导出 → 保存类型选天正3格式(.dwg)**」。
   导出后楼层块名、图纸目录（`CATALOG`）、房间名、逐点标注文字才可读；
   设备本体仍可能残留代理，所以第 2 步与验收门不可省。
2. **另存 DXF，版本 R2018（文件头 `AC1032`）**，不要只交 DWG。
   应用环境为 macOS：无 AutoCAD、无天正，`accoreconsole` 仅 Windows，**无法在产品内自动炸代理**。
3. **代理对象清零或隔离**：交付前自查代理对象数，要求为 0；
   确有残留，必须只落在与算量无关的图层，且在交付说明里点明图层名。
4. **一图一平面**：多户型/多楼层混排时，按图框分开放置或分别导出；
   同文件叠放会被 §10.3 判为「多簇」并要求人工钉底图。
5. **正式输出图（布局/paper space）优先**：模型空间的叠放复制不作交付基准。
6. **中文不要转成乱码占位**：保留图层名与文字原样，代码页 `ANSI_936` 可接受，应用侧负责解码。

### 10.3 收图验收门（应用侧探针，命中即报错回退）

进入 DXF 几何主源（设计 §2.2 Phase 1.2）之前，必须先跑一次结构普查；**任何一项不过即整簇降级为
issue 并停止出数，禁止把「读不到」当成「工程量为 0」**：

| 检查项 | 通过条件 | 不过时的 issue |
|---|---|---|
| 代理对象 | 为 0，或仅落在非角色图层 | `t20_proxy_on_role_layer`（图层名 + 代理数 + 被破坏的几何角色） |
| 真墙图层有几何 | 平行双线间距落 100~250mm 有命中 | `no_wall_geometry`（附候选图层名 + 间距直方图） |
| 平面复制唯一性 | 单簇，或打分后有唯一最高分簇 | `ambiguous_base_drawing`（要求人工钉 `Drawing.is_base`） |
| 代码页/中文 | 图层名与文字可解码 | `encoding_suspect` |

匿名块名（`*U` / `A$C` / `$TwtSys$` / `$equip_U$`）**不作为收图失败条件**：
按 §10.1② 的裁定，块名只用作 `(图层, 块名, ATTDEF 默认值, 插入点簇)` 分组键与溯源字段，
不建「块名 → 清单项」语义映射；设备名一律取块内 `ATTDEF` 的默认值与实例 `attribs`。

### 10.4 DWG→DXF 的两条既有路径与一条禁区

- 首选仍是 §2 的 `libredwg dwg2dxf`；对天正图，**先按 10.2 要 T3+R2018，再谈转换**。
- Windows 侧（设计师或有 AutoCAD 的机器）可用 `accoreconsole` 跑 `_.DXFOUT`：
  SCR 必须 **GBK 编码 + CRLF 换行**，序列为
  `FILEDIA 0 / CMDDIA 0 / _.DXFOUT / <输出路径> / V / 2018 / 16 / _.QUIT / Y`；输出头应为 `AC1032`。
- **禁用 `-EXPORTTOAUTOCAD` 炸代理**：社区实测对 10MB 级天正图 >5 分钟无产出（见
  `docs/reference/community/tianzheng-dwg-parse/README.md`，未经审阅的外部经验，仅作排障对照）。

### 10.5 参考出处

- 本项目实测：`_tz_probe.py`、`docs/QUANTITY_TAKEOFF_MASTER_ARCHITECTURE.md` §12.7
  （「保持块完整、禁止预打散」裁定、`TEL_TITLE` 图框切分、`$TD_AUDIT_GENERATED_*` 孤儿层）。
- 社区单来源（**未经审阅，脚本未在本机执行**）：
  `docs/reference/community/tianzheng-dwg-parse/`。
