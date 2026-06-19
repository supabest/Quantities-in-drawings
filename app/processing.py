"""
Decoration Drawing Automated Measurement Processing Engine
======================================================

This module processes floor plan images using OpenCV to detect:
- Rooms and spaces (area, perimeter)
- Walls (length)
- Doors and windows (count, dimensions)
- Other fixtures

The pipeline works on architectural floor plan images through:
1. Image preprocessing (grayscale, denoise, threshold)
2. Contour detection to find rooms/spaces
3. Wall detection via line segment detection
4. Dimension calculation using scale calibration
"""

import cv2
import numpy as np
import os
import json
import math
from datetime import datetime
from flask import current_app
from app import db
from app.models import Drawing, Measurement


# Minimum contour area to consider as a room (in pixels^2, will be filtered by scale)
MIN_ROOM_AREA_PX = 500
# Maximum contour area to consider as a room (entire image minus margin)
MAX_ROOM_AREA_RATIO = 0.95
# Wall line detection thresholds
WALL_MIN_LENGTH = 30  # pixels
WALL_MAX_GAP = 20  # pixels for line merging
# Default pixels-per-meter (will be overridden by calibration)
DEFAULT_PIXELS_PER_METER = 100


def process_drawing(project, drawings):
    """Main entry point: process all drawings for a project.

    Args:
        project: Project model instance
        drawings: List of Drawing model instances

    Returns:
        dict: Processing results summary
    """
    all_measurements = []
    scale = project.scale or DEFAULT_PIXELS_PER_METER

    for drawing in drawings:
        image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], drawing.filename)
        if not os.path.exists(image_path):
            continue

        measurements = _process_single_image(image_path, drawing, project, scale)
        all_measurements.extend(measurements)

        # Generate annotated image
        annotated_path = _generate_annotated_image(image_path, measurements, drawing, project)

    return {
        'measurement_count': len(all_measurements),
        'drawings_processed': len(drawings)
    }


def _process_single_image(image_path, drawing, project, scale):
    """Process a single floor plan image and extract measurements."""
    # Read image
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"无法读取图片: {image_path}")

    # Update drawing dimensions
    drawing.height, drawing.width = img.shape[:2]

    # Run detection pipeline
    rooms = _detect_rooms(img, scale)
    doors = _detect_doors_and_windows(img, scale)
    walls = _detect_walls(img, scale)

    measurements = []

    # Save room measurements
    for i, room in enumerate(rooms):
        label = _classify_room_type(room, rooms)
        m = Measurement(
            label=label,
            category='room',
            area=room['area_m2'],
            perimeter=room['perimeter_m'],
            length=room.get('length_m', 0),
            width=room.get('width_m', 0),
            unit='m²',
            confidence=room.get('confidence', 0.8),
            project_id=project.id,
            drawing_id=drawing.id
        )
        db.session.add(m)
        measurements.append(m)

    # Save door/window measurements
    for door in doors:
        m = Measurement(
            label=f"{'门' if door['type'] == 'door' else '窗'} #{door.get('index', 0)}",
            category=door['type'],
            length=door.get('width_m', 0),
            height=door.get('height_m', 0),
            count=1,
            unit='个',
            confidence=door.get('confidence', 0.6),
            project_id=project.id,
            drawing_id=drawing.id
        )
        db.session.add(m)
        measurements.append(m)

    # Save wall measurements (total wall length)
    total_wall_length = sum(w.get('length_m', 0) for w in walls)
    if total_wall_length > 0:
        m = Measurement(
            label='墙体总长度',
            category='wall',
            length=total_wall_length,
            count=len(walls),
            unit='m',
            confidence=0.7,
            project_id=project.id,
            drawing_id=drawing.id
        )
        db.session.add(m)
        measurements.append(m)

    db.session.commit()
    return measurements


def _detect_rooms(img, scale):
    """Detect rooms/spaces in a floor plan using contour analysis.

    Pipeline:
    1. Convert to grayscale and denoise
    2. Binary threshold
    3. Find contours
    4. Filter and classify contours as rooms

    Returns:
        list of dict: Each with area_m2, perimeter_m, bounding box, etc.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoised = cv2.medianBlur(gray, 5)
    _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Morphological closing to fill small gaps
    kernel = np.ones((5, 5), np.uint8)
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

    # Find contours
    contours, hierarchy = cv2.findContours(closed, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    img_h, img_w = img.shape[:2]
    img_area = img_h * img_w
    rooms = []

    for i, contour in enumerate(contours):
        area_px = cv2.contourArea(contour)

        # Filter: skip too small or too large
        if area_px < MIN_ROOM_AREA_PX:
            continue
        if area_px > img_area * MAX_ROOM_AREA_RATIO:
            continue

        # Approximate polygon to simplify
        epsilon = 0.02 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)

        # Calculate metrics
        perimeter_px = cv2.arcLength(approx, True)
        area_m2 = area_px / (scale * scale)
        perimeter_m = perimeter_px / scale

        # Bounding rectangle for width/length estimation
        x, y, w, h = cv2.boundingRect(approx)
        length_m = max(w, h) / scale
        width_m = min(w, h) / scale

        # Circularity: 4πA/P² — 1.0 is a perfect circle
        circularity = (4 * math.pi * area_px) / (perimeter_px * perimeter_px) if perimeter_px > 0 else 0

        rooms.append({
            'contour': approx,
            'area_px': area_px,
            'perimeter_px': perimeter_px,
            'area_m2': round(area_m2, 2),
            'perimeter_m': round(perimeter_m, 2),
            'length_m': round(length_m, 2),
            'width_m': round(width_m, 2),
            'circularity': circularity,
            'bbox': (x, y, w, h),
            'confidence': 0.7 if area_m2 > 0.5 else 0.5
        })

    # Sort by area descending (largest rooms first)
    rooms.sort(key=lambda r: r['area_m2'], reverse=True)
    return rooms


def _classify_room_type(room, all_rooms):
    """Try to classify room type based on spatial characteristics.

    Uses heuristics from typical Chinese apartment layouts.
    Returns a label string.
    """
    area = room['area_m2']
    wl_ratio = room['length_m'] / max(room['width_m'], 0.01)
    circularity = room['circularity']

    # Very small spaces
    if area < 1.5:
        return '储藏间/过道'
    if area < 3:
        return '卫生间' if area < 2.5 else '小房间'

    # Bathroom typically small, somewhat square
    if area < 6 and wl_ratio < 1.8:
        return '卫生间'
    if area < 6:
        return '厨房'

    # Kitchen often rectangular
    if 6 < area < 12 and wl_ratio > 1.6:
        return '厨房'

    # Bedrooms
    if 8 < area < 25:
        if wl_ratio > 1.8:
            return '主卧' if area > 15 else '次卧'
        return '次卧' if area < 15 else '主卧'

    # Living/dining room
    if area >= 20:
        return '客厅' if wl_ratio < 1.8 else '客餐厅'

    if area >= 12:
        return '餐厅' if wl_ratio > 1.5 else '客厅'

    return f'房间 ({area:.1f}m²)'


def _detect_doors_and_windows(img, scale):
    """Detect doors and windows in floor plan.

    Uses multiple heuristics:
    - Arced door swings (arc detection in contour regions)
    - Rectangular gaps near wall lines
    - Note: This is a simplified approach; accuracy varies by drawing quality

    Returns:
        list of dict: Door/window detections
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)

    # Detect lines using Hough transform
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=50,
                            minLineLength=30, maxLineGap=15)

    doors = []
    if lines is None:
        return doors

    # Filter lines to find door-sized segments
    # Drawings often show doors as a line with an arc
    # We look for short line segments near wall intersections
    for line in lines:
        x1, y1, x2, y2 = line[0]
        length = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
        length_m = length / scale

        # Door segment typically 0.7-1.2m in real life
        if 0.6 < length_m < 1.5:
            doors.append({
                'type': 'door',
                'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                'width_m': round(length_m, 2),
                'height_m': 2.1,  # standard door height
                'confidence': 0.5,
                'index': len(doors) + 1
            })

    return doors


def _detect_walls(img, scale):
    """Detect wall lines in floor plan.

    Uses Canny edge detection + Hough line transform
    to identify wall segments.

    Returns:
        list of dict: Wall segments with length
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120, apertureSize=3)

    # Dilate edges to connect broken wall lines
    kernel = np.ones((2, 2), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=1)

    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80,
                            minLineLength=WALL_MIN_LENGTH, maxLineGap=WALL_MAX_GAP)

    walls = []
    if lines is None:
        return walls

    for line in lines:
        x1, y1, x2, y2 = line[0]
        length = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
        length_m = length / scale

        # Filter: walls should be meaningful length
        if length_m > 0.3:
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            walls.append({
                'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                'length_m': round(length_m, 2),
                'angle': round(angle, 1)
            })

    return walls


def _generate_annotated_image(image_path, measurements, drawing, project):
    """Generate an annotated version of the image with measurement overlays."""
    img = cv2.imread(image_path)
    if img is None:
        return None

    overlay = img.copy()
    scale = project.scale or DEFAULT_PIXELS_PER_METER

    # Colors (BGR format)
    color_room = (65, 167, 79)      # green
    color_wall = (66, 133, 244)     # blue
    color_door = (234, 134, 47)      # orange
    color_window = (219, 68, 55)     # red
    color_text = (0, 0, 0)          # black
    colors = {'room': color_room, 'wall': color_wall,
              'door': color_door, 'window': color_window}

    # Redraw room contours from measurements
    # (contours are not stored in DB; we re-detect for annotation)
    rooms = _detect_rooms(img, scale)
    for room in rooms:
        cv2.drawContours(overlay, [room['contour']], -1, color_room, 2)
        # Fill with semi-transparent
        cv2.drawContours(overlay, [room['contour']], -1,
                         (color_room[0] // 3, color_room[1] // 3, color_room[2] // 3), -1)

        # Label area
        x, y, w, h = room['bbox']
        label = f"{room['area_m2']}m²"
        cv2.putText(overlay, label, (x + 5, y + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color_text, 2)

    # Draw walls
    walls = _detect_walls(img, scale)
    for wall in walls:
        cv2.line(overlay, (wall['x1'], wall['y1']),
                 (wall['x2'], wall['y2']), color_wall, 2)

    # Draw doors
    doors = _detect_doors_and_windows(img, scale)
    for door in doors:
        cv2.line(overlay, (door['x1'], door['y1']),
                 (door['x2'], door['y2']), color_door, 3)
        mid_x = (door['x1'] + door['x2']) // 2
        mid_y = (door['y1'] + door['y2']) // 2
        cv2.putText(overlay, f"门 {door['width_m']}m",
                    (mid_x, mid_y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color_text, 1)

    # Blend overlay
    result = cv2.addWeighted(overlay, 0.7, img, 0.3, 0)

    # Save annotated image
    annotated_filename = f"annotated_{drawing.filename}"
    annotated_path = os.path.join(current_app.config['UPLOAD_FOLDER'], annotated_filename)
    cv2.imwrite(annotated_path, result)

    drawing.annotated_path = annotated_filename
    db.session.commit()

    return annotated_path


def _detect_scale_from_reference(img, reference_text):
    """Attempt to detect scale from a reference dimension in the image.

    Looks for dimension lines/text in the drawing.
    This is a placeholder — full OCR implementation would use EasyOCR/Tesseract.
    """
    # For now, return default scale
    # Future: Use OCR to read dimension labels and calculate scale
    return DEFAULT_PIXELS_PER_METER
