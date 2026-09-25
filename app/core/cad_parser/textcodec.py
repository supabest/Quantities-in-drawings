"""AutoCAD 文本编码解码（Phase 5b · 装修支路）。

问题：原生 DXF（尤其经 ODA/AutoCAD 导出的中文图）里，中文字符不以 Unicode 直接
存储，而是以 ``\\M+5HHHH`` 形式转义——``5`` 为字体/代码页槽位标记，``HHHH`` 为该
字符的 **GBK 双字节十六进制**。ezdxf 读出的是原始转义串，直接匹配中文会全部落空。

本模块提供 ``decode_acad_text``：把 ``\\M+5HHHH`` 序列按 GBK 还原为中文，
并顺带清理常见 MTEXT 内联格式码，供图层/块名/房间标签匹配使用。
"""
from __future__ import annotations

import re

# \M+5HHHH —— 反斜杠 + "M+" + 字体槽数字(通常 5) + 4 位十六进制 GBK 码
_MGBK = re.compile(r'\\M\+([0-9A-Fa-f])([0-9A-Fa-f]{4})')
# 常见 MTEXT 内联格式码：\P(换行) \A1; \f...; \H...; \C1; \W; \Q; \T; \{ \} 等
_FMT = re.compile(r'\\[PpAaCcHhQqTtWwOoKkLl~]')
_FMT_WITH_ARG = re.compile(r'\\[FAHCTWQ][^;]*;')


def decode_acad_text(s: str) -> str:
    """把 AutoCAD 文本串还原为可读 Unicode。

    - ``\\M+5HHHH`` → 按 GBK 解码为中文；无法解码时保留原样。
    - 去掉常见 MTEXT 内联格式码（不影响几何/匹配）。
    """
    if not s:
        return ''

    def _rep(m: re.Match) -> str:
        hexcode = m.group(2)
        try:
            return bytes.fromhex(hexcode).decode('gbk')
        except (ValueError, UnicodeDecodeError):
            return m.group(0)

    out = _MGBK.sub(_rep, s)
    out = _FMT_WITH_ARG.sub('', out)
    out = _FMT.sub('', out)
    out = out.replace('\\N', '\n').replace('%%d', '°').replace('%%c', 'Ø')
    return out.strip()


def has_cjk(s: str) -> bool:
    """是否含中文字符（解码后判定用）。"""
    return bool(s) and bool(re.search(r'[\u4e00-\u9fff]', s))
