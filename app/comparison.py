"""
Historical BOQ Comparison Engine — v3.1
=======================================
Compares current project measurements against historical project BOQs to:
1. Find missing items that should be present (补漏对照)
2. Auto-fill hidden items with 【历史参照】 source tag
3. Reasonableness check against historical baselines
4. Output comparison report

Spec: Project Instructions §3.2, §3.3
"""

import json
import re
from datetime import datetime
from collections import defaultdict

import openpyxl
from flask import current_app

from app import db
from app.models import (
    Measurement, HistoricalProject, DIVISIONS, HIDDEN_ITEMS,
    SOURCE_TAGS, CONFIDENCE_LEVELS
)


# ── Division Name Mapping ──

DIVISION_NAMES = {
    '楼地面': 1, '地面': 1, 'floor': 1,
    '墙柱面': 2, '墙面': 2, '墙': 2, 'wall': 2,
    '天棚': 3, '天花': 3, '吊顶': 3, 'ceiling': 3,
    '门窗': 4, '门': 4, '窗': 4, 'door': 4, 'window': 4,
    '踢脚': 5, '踢脚线': 5, 'baseboard': 5,
    '固定柜': 6, '柜台': 6, '橱柜': 6, 'cabinet': 6,
    '给排水': 7, 'plumbing': 7,
    '电气': 8, 'electric': 8,
}

# Reasonableness reference ranges (from v3.1 spec)
REASONABLENESS_RANGES = {
    'wall_floor_ratio': {'residential': (2.0, 3.0), 'showroom': (2.5, 3.5), 'default': (1.5, 4.0)},
    'light_per_m2': {'residential': (0.10, 0.20), 'showroom': (0.08, 0.15), 'default': (0.05, 0.30)},
    'socket_per_m2': {'residential': (0.08, 0.15), 'showroom': (0.05, 0.10), 'default': (0.05, 0.20)},
    'baseboard_ratio': (0.6, 1.1),
    'waterproof_ratio': (1.3, None),  # >= 1.3
}


# ════════════════════════════════════════════
# Phase 1: Parse Historical BOQ Excel
# ════════════════════════════════════════════

def parse_historical_excel(file_path):
    """Parse a historical BOQ Excel file and extract structured items.

    Supports common BOQ formats:
    - Standard 11-column format (from this system)
    - Free-form with column headers in first row
    - Chinese/English column headers

    Returns:
        dict: {
            'project_name': str,
            'divisions': { div_id: [{item_dict}, ...] },
            'stats': { total_items, wall_floor_ratio, ... },
            'raw_items': [all items flat list]
        }
    """
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return _empty_result()

    # Detect header row and column mapping
    header_row, col_map = _detect_columns(rows)

    # Parse data rows
    items = []
    for row in rows[header_row + 1:]:
        item = _parse_row(row, col_map)
        if item and item.get('name'):
            items.append(item)

    wb.close()

    # Organize by division
    divisions = defaultdict(list)
    for item in items:
        div_id = _guess_division(item.get('name', ''), item.get('division_name', ''))
        item['division_id'] = div_id
        divisions[div_id].append(item)

    # Calculate stats
    stats = _calculate_stats(items, divisions)

    return {
        'project_name': _extract_project_name(rows[:5]),
        'divisions': dict(divisions),
        'stats': stats,
        'raw_items': items,
        'item_count': len(items),
    }


def _detect_columns(rows):
    """Find the header row and map columns."""
    keywords = {
        'name': ['项目名称', '名称', '项目', 'name', 'item', '子目'],
        'division': ['分部', '分项', 'division', 'section'],
        'features': ['项目特征', '特征', 'features', 'description'],
        'position': ['部位', '位置', 'position', 'location'],
        'formula': ['计算式', '计算', 'formula', 'expression'],
        'quantity': ['工程量', '数量', '量', 'quantity', 'qty'],
        'unit': ['单位', 'unit', '计量单位'],
        'confidence': ['置信度', 'confidence'],
        'source': ['来源', 'source', '标签', '来源标签'],
        'remarks': ['备注', 'remarks', 'note', '说明'],
    }

    for i, row in enumerate(rows[:10]):
        if not row:
            continue
        col_map = {}
        for j, cell in enumerate(row):
            if cell is None:
                continue
            text = str(cell).strip()
            for key, kws in keywords.items():
                if any(kw in text for kw in kws):
                    col_map[key] = j
                    break
        if col_map.get('name') is not None:  # Found name column = header row
            return i, col_map

    # Fallback: no header detected, assume first row is header
    return 0, {'name': 0, 'quantity': 3, 'unit': 4}


def _parse_row(row, col_map):
    """Parse a single row into an item dict."""
    item = {}
    for key, col_idx in col_map.items():
        if col_idx < len(row) and row[col_idx] is not None:
            item[key] = str(row[col_idx]).strip()
        else:
            item[key] = ''

    # Parse numeric quantity
    qty_str = item.get('quantity', '0')
    try:
        qty = float(re.sub(r'[^\d.]', '', qty_str))
    except (ValueError, TypeError):
        qty = 0
    item['quantity_float'] = qty

    return item


def _guess_division(name, division_text):
    """Guess which division (1-8) an item belongs to."""
    # Try division_text first
    if division_text:
        for keyword, div_id in DIVISION_NAMES.items():
            if keyword in division_text:
                return div_id

    # Fall back to name
    if name:
        for keyword, div_id in DIVISION_NAMES.items():
            if keyword in name:
                return div_id

    return 0  # Unknown


def _calculate_stats(items, divisions):
    """Calculate key statistics from historical BOQ."""
    wall_area = sum(i.get('quantity_float', 0) for i in divisions.get(2, []))
    floor_area = sum(i.get('quantity_float', 0) for i in divisions.get(1, []))
    light_count = sum(i.get('quantity_float', 0) for i in divisions.get(8, [])
                      if '灯' in i.get('name', ''))
    socket_count = sum(i.get('quantity_float', 0) for i in divisions.get(8, [])
                       if '插座' in i.get('name', '') or '开关' in i.get('name', ''))

    return {
        'total_items': len(items),
        'floor_area': floor_area,
        'wall_area': wall_area,
        'wall_floor_ratio': round(wall_area / floor_area, 2) if floor_area > 0 else 0,
        'light_per_m2': round(light_count / floor_area, 2) if floor_area > 0 else 0,
        'socket_per_m2': round(socket_count / floor_area, 2) if floor_area > 0 else 0,
        'division_counts': {str(k): len(v) for k, v in divisions.items()},
        'division_items': {str(k): [i.get('name', '') for i in v] for k, v in divisions.items()},
    }


def _extract_project_name(header_rows):
    """Try to extract project name from header area."""
    for row in header_rows:
        if not row:
            continue
        for cell in row:
            if cell and '项目' in str(cell):
                return str(cell)
    return '历史清单'


def _empty_result():
    return {
        'project_name': '未知',
        'divisions': {},
        'stats': {},
        'raw_items': [],
        'item_count': 0,
    }


# ════════════════════════════════════════════
# Phase 2: Comparison Logic
# ════════════════════════════════════════════

def compare_with_historical(project_id, historical_id):
    """Run comparison between current project and historical BOQ.

    Returns:
        dict: {
            'missing_items': [{name, division_id, reason, ...}],
            'stats_comparison': {...},
            'warnings': [...],
            'filled_items': [Measurement objects added],
        }
    """
    hist = HistoricalProject.query.get(historical_id)
    if not hist or not hist.summary_json:
        return {'error': '历史清单数据不完整'}

    hist_data = json.loads(hist.summary_json) if isinstance(hist.summary_json, str) else hist.summary_json
    current_measurements = Measurement.query.filter_by(project_id=project_id).all()

    # Build lookup of current items by division
    current_by_div = defaultdict(list)
    for m in current_measurements:
        current_by_div[m.division_id].append(m)

    # Get labels for quick dedup
    current_labels = set()
    current_by_label = {}
    for m in current_measurements:
        label = (m.label or '').strip().replace(' ', '')
        current_labels.add(label)
        current_by_label[label] = m

    missing = []
    filled = []

    # Compare each division
    for div_id, div_name, unit, _ in DIVISIONS:
        hist_items = hist_data.get('stats', {}).get('division_items', {}).get(str(div_id), [])

        for hist_name in hist_items:
            # Clean name for comparison
            clean_name = hist_name.strip().replace(' ', '')
            if not clean_name:
                continue

            # Check if item exists in current project
            exists = False
            for cur_label in current_labels:
                # Fuzzy match: try substring and keyword overlap
                if _items_match(clean_name, cur_label):
                    exists = True
                    break

            if not exists:
                # Guess quantity and unit from historical data
                hist_qty = _find_item_quantity(hist, clean_name, div_id)

                missing.append({
                    'name': hist_name,
                    'division_id': div_id,
                    'division_name': div_name,
                    'historical_quantity': hist_qty,
                    'unit': unit,
                    'reason': '历史清单有此项目，当前算量未检出',
                })

    # Calculate stats comparison
    stats_comparison = _compare_stats(hist_data, current_measurements)

    # Generate warnings
    warnings = _generate_comparison_warnings(stats_comparison)

    return {
        'missing_items': missing,
        'stats_comparison': stats_comparison,
        'warnings': warnings,
        'filled_items': filled,
        'historical': {
            'name': hist.name,
            'item_count': hist_data.get('stats', {}).get('total_items', 0),
        },
    }


def _items_match(name1, name2):
    """Check if two item names refer to the same thing."""
    if not name1 or not name2:
        return False

    # Direct match
    if name1 == name2:
        return True
    if name1 in name2 or name2 in name1:
        return True

    # Extract core keywords (strip common prefixes/suffixes)
    keywords1 = set(re.findall(r'[一-鿿\w]+', name1))
    keywords2 = set(re.findall(r'[一-鿿\w]+', name2))

    # Ignore location prefixes (客厅-, 主卧-, etc.)
    common = keywords1 & keywords2
    if len(common) >= 2:
        return True

    return False


def _find_item_quantity(hist, item_name, div_id):
    """Find typical quantity for this item in historical data."""
    try:
        data = json.loads(hist.summary_json) if isinstance(hist.summary_json, str) else hist.summary_json
        items = data.get('stats', {}).get('division_items', {}).get(str(div_id), [])
        counts = data.get('stats', {}).get('division_counts', {}).get(str(div_id), 0)
        # Simplified: return count as rough estimate
        return counts
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0


def _compare_stats(hist_data, current_measurements):
    """Compare current project stats against historical baselines."""
    curr_floor = sum(m.area or 0 for m in current_measurements if m.division_id == 1)
    curr_walls = sum(m.area or 0 for m in current_measurements if m.division_id == 2)

    hist_stats = hist_data.get('stats', {})
    hist_floor = hist_stats.get('floor_area', 0)
    hist_ratio = hist_stats.get('wall_floor_ratio', 0)

    curr_ratio = round(curr_walls / curr_floor, 2) if curr_floor > 0 else 0

    deviation = None
    if hist_ratio and curr_ratio:
        deviation = round((curr_ratio - hist_ratio) / hist_ratio * 100, 1)

    return {
        'current': {
            'floor_area': round(curr_floor, 2),
            'wall_floor_ratio': curr_ratio,
        },
        'historical': {
            'floor_area': hist_floor,
            'wall_floor_ratio': hist_ratio,
        },
        'deviation_pct': deviation,
        'is_alarm': deviation is not None and abs(deviation) > 30,
    }


def _generate_comparison_warnings(stats):
    """Generate warnings based on comparison results."""
    warnings = []
    if stats.get('is_alarm'):
        warnings.append(
            f'⚠️ 墙地面积比偏差 {stats["deviation_pct"]}%（超出±30%阈值），'
            f'当前 {stats["current"]["wall_floor_ratio"]} vs '
            f'历史 {stats["historical"]["wall_floor_ratio"]}，请核查工程量'
        )
    return warnings


# ════════════════════════════════════════════
# Phase 3: Auto-fill Missing Items
# ════════════════════════════════════════════

def auto_fill_missing(project_id, missing_items):
    """Auto-fill missing items into the project measurements.

    Each filled item gets source_tag = '历史参照', confidence = medium.
    """
    filled = []
    for item in missing_items:
        # Skip items that already exist (double-check)
        existing = Measurement.query.filter_by(
            project_id=project_id,
            division_id=item['division_id'],
        ).filter(Measurement.label.like(f'%{item["name"][:10]}%')).first()

        if existing:
            continue

        m = Measurement(
            label=f'【历史参照】{item["name"]}',
            division_id=item['division_id'],
            category='fixture',
            count=int(item.get('historical_quantity', 1)) or 1,
            unit=item.get('unit', '项'),
            source_tag='历史参照',
            source_tag_icon='📋',
            confidence_level='medium',
            confidence_icon='🟡',
            confidence=0.5,
            position_desc='（历史清单参照补入）',
            formula_text=f'参照历史同类项目清单补入，图纸未显示',
            project_features=item['name'],
            remarks=f'【历史参照】参照历史清单补入，图纸未明确显示此项目。置信度🟡中，建议复核后用于报价。',
            project_id=project_id,
        )
        db.session.add(m)
        filled.append(m)

    db.session.commit()
    return filled
