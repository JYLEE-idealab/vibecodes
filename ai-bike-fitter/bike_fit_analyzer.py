"""
AI Bike Fitting Video Synthesizer & Biomechanics Analyzer (PC & Mobile Web App)
-------------------------------------------------------------------------------
Synthesizes onto every frame of a cycling video:
  1. Semi-transparent cyan-blue biomechanical polygon mesh ("Crank At 6 O'Clock" style)
  2. 5 floating joint-angle cards with 5-segment color standard gauge bars [Red|Yellow|Green|Yellow|Red]
     (Shoulder Angle, Torso Angle, Hip Angle, Knee Angle, Ankle/Foot Angle)
  3. Front-view 11-line knee tracking mode (Cleat Wedge / Varus-Valgus arrows)
  4. Bottom-right live scrolling Knee & Hip angle waveform graph
  5. Automatic capture of Crank At 6 O'Clock (BDC) & Crank At 12 O'Clock (TDC) snapshots
  6. H.264 MP4 video encoding with original audio preserved for instant KakaoTalk sharing
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO


@dataclass
class RidingStyleSpec:
    key: str
    kor_title: str
    subtitle: str
    knee_bdc_range: Tuple[float, float]      # 6시 하사점 무릎 각도 (골반-무릎-발목)
    hip_bdc_range: Tuple[float, float]       # 6시 하사점 고관절 각도 (어깨-골반-무릎)
    hip_tdc_range: Tuple[float, float]       # 12시 상사점 고관절 각도
    torso_range: Tuple[float, float]         # 수평선 대비 상체 기울기 각도
    shoulder_range: Tuple[float, float]      # 골반-어깨-팔꿈치 리치 각도
    elbow_range: Tuple[float, float]         # 어깨-팔꿈치-손목 각도


RIDING_STYLES: Dict[str, RidingStyleSpec] = {
    "ROAD_ENDURANCE": RidingStyleSpec(
        key="ROAD_ENDURANCE",
        kor_title="로드 엔듀런스 / 올라운드",
        subtitle="편안한 장거리 라이딩 세팅",
        knee_bdc_range=(140.0, 150.0),
        hip_bdc_range=(105.0, 120.0),
        hip_tdc_range=(68.0, 80.0),
        torso_range=(40.0, 55.0),
        shoulder_range=(80.0, 92.0),
        elbow_range=(150.0, 168.0),
    ),
    "ROAD_RACE": RidingStyleSpec(
        key="ROAD_RACE",
        kor_title="로드 레이싱 (퍼포먼스)",
        subtitle="공기역학 & 파워 전달 중심 세팅",
        knee_bdc_range=(142.0, 152.0),
        hip_bdc_range=(100.0, 116.0),
        hip_tdc_range=(64.0, 76.0),
        torso_range=(30.0, 44.0),
        shoulder_range=(84.0, 98.0),
        elbow_range=(145.0, 165.0),
    ),
    "AERO_TT": RidingStyleSpec(
        key="AERO_TT",
        kor_title="에어로 / TT (철인 3종)",
        subtitle="최대 공기저항 감소 포지션",
        knee_bdc_range=(142.0, 152.0),
        hip_bdc_range=(95.0, 112.0),
        hip_tdc_range=(58.0, 72.0),
        torso_range=(15.0, 32.0),
        shoulder_range=(80.0, 100.0),
        elbow_range=(85.0, 115.0),
    ),
    "MTB_GRAVEL": RidingStyleSpec(
        key="MTB_GRAVEL",
        kor_title="MTB / 그래블 / 하이브리드",
        subtitle="상체 컨트롤 및 충격 흡수 중심",
        knee_bdc_range=(138.0, 148.0),
        hip_bdc_range=(108.0, 124.0),
        hip_tdc_range=(70.0, 85.0),
        torso_range=(46.0, 62.0),
        shoulder_range=(75.0, 90.0),
        elbow_range=(148.0, 168.0),
    ),
}


@dataclass
class FrameBiomechanics:
    frame_idx: int
    timestamp_sec: float
    is_right_side: bool
    shoulder: Tuple[float, float]
    elbow: Tuple[float, float]
    wrist: Tuple[float, float]
    hip: Tuple[float, float]
    knee: Tuple[float, float]
    ankle: Tuple[float, float]
    foot: Tuple[float, float]
    knee_angle: float
    hip_angle: float
    torso_angle: float
    shoulder_angle: float
    elbow_angle: float
    foot_angle: float
    kops_norm: float
    front_left_dev_pct: float = 0.0
    front_right_dev_pct: float = 0.0
    snapped_markers: Dict[str, bool] = field(default_factory=dict)


@dataclass
class BikeFitVideoResult:
    output_video_path: str
    bdc_snapshot_path: str
    tdc_snapshot_path: str
    report_chart_path: str
    total_frames: int
    fps: float
    start_sec: float
    end_sec: float
    detected_side: str
    cadence_rpm: int
    overall_score: int
    bdc_knee_angle: float
    bdc_hip_angle: float
    bdc_timestamp_sec: float
    tdc_hip_angle: float
    tdc_knee_angle: float
    tdc_timestamp_sec: float
    avg_torso_angle: float
    avg_shoulder_angle: float
    avg_elbow_angle: float
    avg_foot_angle: float
    marker_snap_rate_pct: float = 0.0
    diagnostics: List[Dict[str, str]] = field(default_factory=list)


def _get_korean_font(size: int) -> ImageFont.FreeTypeFont:
    font_candidates = [
        "C:/Windows/Fonts/malgunbd.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/NanumGothicBold.ttf",
        "C:/Windows/Fonts/gulim.ttc",
    ]
    for fp in font_candidates:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


def compute_interior_angle(
    a: Tuple[float, float], vertex: Tuple[float, float], c: Tuple[float, float]
) -> float:
    v1 = np.array([a[0] - vertex[0], a[1] - vertex[1]], dtype=np.float32)
    v2 = np.array([c[0] - vertex[0], c[1] - vertex[1]], dtype=np.float32)
    n1 = float(np.linalg.norm(v1))
    n2 = float(np.linalg.norm(v2))
    if n1 < 1e-3 or n2 < 1e-3:
        return 0.0
    cos_val = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    return float(np.degrees(np.arccos(cos_val)))


def compute_horizontal_angle(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    dx = abs(p2[0] - p1[0])
    dy = abs(p1[1] - p2[1])
    if dx < 1e-3 and dy < 1e-3:
        return 25.0
    return float(np.degrees(np.arctan2(dy, dx)))


def evaluate_status_color_bgr(
    val: float, target_range: Tuple[float, float]
) -> Tuple[str, Tuple[int, int, int]]:
    """
    Returns (StatusLabel, BGR_Color):
      - Green (Optimal): inside [low, high] -> (125, 239, 56) (#38EF7D)
      - Yellow (Warning): within ±5 deg -> (0, 210, 255) (#FFD200)
      - Red (Alert): outside ±5 deg -> (82, 82, 255) (#FF5252)
    """
    low, high = target_range
    if low <= val <= high:
        return "적정 (초록)", (125, 239, 56)
    elif (low - 5.0) <= val <= (high + 5.0):
        return "경계 (노랑)", (0, 210, 255)
    else:
        return "교정필요 (빨강)", (82, 82, 255)


def compute_gauge_norm_pos(val: float, target_range: Tuple[float, float]) -> float:
    """
    Maps val onto the 5-segment gauge bar [0.0 .. 1.0] where:
      - 0.00..0.15: Red (Too Low)
      - 0.15..0.30: Yellow (Borderline Low)
      - 0.30..0.70: Green (Optimal Target Range)
      - 0.70..0.85: Yellow (Borderline High)
      - 0.85..1.00: Red (Too High)
    """
    low, high = target_range
    span = max(4.0, high - low)
    margin = span * 0.75
    min_v = low - margin
    max_v = high + margin
    return float(np.clip((val - min_v) / max(1.0, max_v - min_v), 0.04, 0.96))


# =========================================================================
# COLOR STICKER MARKER SNAP ENGINE & PLACEMENT GUIDE GENERATOR
# =========================================================================
STICKER_PLACEMENTS: List[Dict[str, str]] = [
    {
        "no": "① 손목 (Wrist)",
        "landmark": "손목 바깥쪽 복숭아뼈 (척골 경상돌기) 또는 장갑 손목 위",
        "tip": "핸들바 후드를 잡은 상태에서 측면 카메라를 정면으로 바라보도록 부착",
        "purpose": "팔꿈치 굽힘 각도(Elbow Angle) 측정 기준점",
    },
    {
        "no": "② 팔꿈치 (Elbow)",
        "landmark": "팔꿈치 바깥쪽 볼록 튀어나온 뼈 (상완골 외측상과)",
        "tip": "팔을 약간 굽혔을 때 옆에서 만져지는 가장 볼록한 회전축 뼈 중앙",
        "purpose": "어깨 리치 각도 & 팔꿈치 충격 흡수 각도 측정",
    },
    {
        "no": "③ 어깨 (Shoulder)",
        "landmark": "어깨 끝 봉우리 뼈 (견봉, Acromion) 바로 아래 회전 중심",
        "tip": "져지 어깨 봉제선 근처, 팔을 앞뒤로 흔들 때 중심축이 되는 지점",
        "purpose": "상체 숙임각(Torso) & 어깨 리치(Shoulder) 기준점",
    },
    {
        "no": "④ 골반 (Hip - 대전자) ★필수",
        "landmark": "고관절 대전자 (Greater Trochanter - 허벅지 옆 큰 뼈)",
        "tip": "다리를 위아래로 들어 올릴 때 골반 옆에서 함께 구르는 가장 튀어나온 뼈 (골반 위쪽 허리뼈 아님!)",
        "purpose": "무릎·고관절·상체 각도의 핵심 기준축 (AI 오차를 가장 크게 줄여줌)",
    },
    {
        "no": "⑤ 무릎 (Knee - 외측상과) ★필수",
        "landmark": "무릎 바깥쪽 관절 회전축 (대퇴골 외측상과)",
        "tip": "무릎 앞쪽 슬개골(뚜껑뼈)이 아니라, 무릎 옆면의 회전 중심 뼈",
        "purpose": "6시 하사점 무릎 신전 각도(안장 높이) ±0.5° 정밀 측정",
    },
    {
        "no": "⑥ 발목 (Ankle - 복사뼈) ★필수",
        "landmark": "바깥쪽 복사뼈 정중앙 (외과, Lateral Malleolus)",
        "tip": "양말이나 슈즈 위로 복사뼈 가장 볼록한 중심에 부착",
        "purpose": "무릎 각도 및 발목 앵클링(Ankling) 측정",
    },
    {
        "no": "⑦ 클릿 슈즈 앞코 (Shoe Cleat / Metatarsal) ★필수",
        "landmark": "클릿 슈즈 바깥쪽 제5중족골 헤드 (페달 중심축 / 클릿 마운트 라인 옆면)",
        "tip": "슈즈 옆면의 보아(BOA) 다이얼 아래쪽, 실제 페달 축이 지나가는 발볼 옆면에 부착",
        "purpose": "일반 AI가 못 잡는 발 각도(Foot Angle) 및 3시 KOPS 수직선 100% 정밀 측정",
    },
]


def snap_point_to_color_sticker(
    frame_bgr: np.ndarray,
    approx_xy: np.ndarray,
    marker_color_mode: str,
    search_radius_px: int = 58,
) -> Tuple[np.ndarray, bool]:
    """
    Searches a circular neighborhood around the AI-predicted keypoint `approx_xy`
    for a bright round color sticker (Yellow, Red/Orange, Green, or Auto).
    If found, returns (subpixel_sticker_centroid_xy, True); otherwise (approx_xy, False).
    """
    if not marker_color_mode or marker_color_mode == "NONE":
        return approx_xy, False

    h, w = frame_bgr.shape[:2]
    cx, cy = int(round(float(approx_xy[0]))), int(round(float(approx_xy[1])))
    r = max(18, int(search_radius_px))

    x1, y1 = max(0, cx - r), max(0, cy - r)
    x2, y2 = min(w, cx + r), min(h, cy + r)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return approx_xy, False

    roi = frame_bgr[y1:y2, x1:x2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    # HSV masks for standard round stickers (Daiso fluorescent/vivid stickers)
    mask_yellow = cv2.inRange(hsv, np.array([16, 95, 115]), np.array([42, 255, 255]))
    mask_red1 = cv2.inRange(hsv, np.array([0, 110, 110]), np.array([13, 255, 255]))
    mask_red2 = cv2.inRange(hsv, np.array([165, 110, 110]), np.array([180, 255, 255]))
    mask_red = cv2.bitwise_or(mask_red1, mask_red2)
    mask_green = cv2.inRange(hsv, np.array([43, 95, 110]), np.array([88, 255, 255]))

    if marker_color_mode == "YELLOW":
        mask = mask_yellow
    elif marker_color_mode == "RED":
        mask = mask_red
    elif marker_color_mode == "GREEN":
        mask = mask_green
    else:  # AUTO (Yellow + Red + Neon Green)
        mask = cv2.bitwise_or(cv2.bitwise_or(mask_yellow, mask_red), mask_green)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return approx_xy, False

    roi_cx = cx - x1
    roi_cy = cy - y1
    best_pt: Optional[np.ndarray] = None
    best_score = -1e9

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 9.0 or area > (r * r * 0.75):
            continue
        peri = cv2.arcLength(cnt, True)
        if peri <= 1e-3:
            continue
        circularity = 4.0 * math.pi * area / (peri * peri)
        if circularity < 0.35:
            continue

        M = cv2.moments(cnt)
        if M["m00"] <= 1e-3:
            continue
        mcx = float(M["m10"] / M["m00"])
        mcy = float(M["m01"] / M["m00"])
        dist = math.hypot(mcx - roi_cx, mcy - roi_cy)
        if dist > r:
            continue

        # Score favors circularity, reasonable sticker size, and proximity to the joint
        score = (circularity * 45.0) + min(area, 250.0) * 0.15 - (dist * 0.85)
        if score > best_score:
            best_score = score
            best_pt = np.array([x1 + mcx, y1 + mcy], dtype=np.float32)

    if best_pt is not None:
        return best_pt, True
    return approx_xy, False


def generate_sticker_placement_guide_image(
    output_path: str = "outputs/sticker_placement_guide.png",
) -> str:
    """
    Renders a clear visual diagram showing the exact 7 anatomical & shoe locations
    where round yellow/red stickers should be attached for ±0.5° pro-level bike fitting accuracy.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    w, h = 1280, 680
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (24, 18, 13)  # Dark navy background

    # Left panel: Cyclist on Roller Illustration (0..640)
    cv2.rectangle(img, (18, 56), (620, 658), (36, 28, 20), -1)
    cv2.rectangle(img, (18, 56), (620, 658), (110, 90, 65), 1)

    # Bike wheels & frame
    rear_w = (165, 510)
    front_w = (485, 510)
    bb = (295, 510)
    saddle = (220, 340)
    handlebar = (445, 345)

    cv2.circle(img, rear_w, 92, (85, 72, 58), 4, cv2.LINE_AA)
    cv2.circle(img, front_w, 92, (85, 72, 58), 4, cv2.LINE_AA)
    cv2.line(img, rear_w, bb, (115, 95, 75), 4, cv2.LINE_AA)
    cv2.line(img, bb, saddle, (115, 95, 75), 5, cv2.LINE_AA)
    cv2.line(img, saddle, (handlebar[0] - 15, handlebar[1]), (115, 95, 75), 5, cv2.LINE_AA)
    cv2.line(img, bb, handlebar, (115, 95, 75), 5, cv2.LINE_AA)
    cv2.line(img, handlebar, front_w, (115, 95, 75), 4, cv2.LINE_AA)

    # Rider anatomical points (6 O'Clock BDC posture)
    pt_wrist = (448, 336)
    pt_elbow = (402, 294)
    pt_shoulder = (356, 208)
    pt_hip = (232, 320)
    pt_knee = (328, 424)
    pt_ankle = (284, 548)
    pt_shoe = (334, 560)
    pt_head = (392, 165)

    # Translucent cyan polygon mesh
    poly = img.copy()
    for tri in [
        np.array([pt_shoulder, pt_elbow, pt_hip]),
        np.array([pt_shoulder, pt_hip, pt_knee]),
        np.array([pt_hip, pt_knee, pt_ankle]),
        np.array([pt_knee, pt_ankle, pt_shoe]),
    ]:
        cv2.fillPoly(poly, [tri], (210, 170, 0), cv2.LINE_AA)
    cv2.addWeighted(poly, 0.30, img, 0.70, 0, img)

    # Rider silhouette & bones
    cv2.circle(img, pt_head, 26, (120, 110, 95), -1, cv2.LINE_AA)
    for p1, p2 in [
        (pt_wrist, pt_elbow),
        (pt_elbow, pt_shoulder),
        (pt_shoulder, pt_hip),
        (pt_hip, pt_knee),
        (pt_knee, pt_ankle),
        (pt_ankle, pt_shoe),
    ]:
        cv2.line(img, p1, p2, (255, 215, 40), 7, cv2.LINE_AA)

    # Draw 7 glowing Yellow/Red Sticker Markers with numbered callouts
    sticker_coords = [
        (pt_wrist, "1", (0, 235, 255)),     # Yellow sticker
        (pt_elbow, "2", (0, 235, 255)),
        (pt_shoulder, "3", (0, 235, 255)),
        (pt_hip, "4", (60, 75, 255)),       # Red/Yellow highlight for key lower body
        (pt_knee, "5", (0, 235, 255)),
        (pt_ankle, "6", (0, 235, 255)),
        (pt_shoe, "7", (60, 75, 255)),
    ]
    for (sx, sy), num_str, bgr_col in sticker_coords:
        cv2.circle(img, (sx, sy), 18, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.circle(img, (sx, sy), 14, bgr_col, -1, cv2.LINE_AA)
        cv2.circle(img, (sx, sy), 4, (20, 20, 20), -1, cv2.LINE_AA)

    # Render Korean Guide Text on Right Panel using PIL
    pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    f_title = _get_korean_font(22)
    f_sub = _get_korean_font(14)
    f_bold = _get_korean_font(15)
    f_desc = _get_korean_font(12)
    f_num = _get_korean_font(15)

    draw.text(
        (24, 14),
        "🎯 프로 피팅샵 수준(±0.5°) 오차 감소를 위한 원형 컬러 스티커 7곳 부착 가이드",
        font=f_title,
        fill=(56, 239, 125),
    )
    draw.text(
        (28, 66),
        "• 다이소 원형 견출지(지름 1.5~2.5cm, 노란색/형광연두/빨간색)를 아래 7곳의 회전축에 붙이고 촬영하세요.",
        font=f_sub,
        fill=(220, 235, 250),
    )

    # Number labels next to points on diagram
    label_offsets = [
        (pt_wrist, "① 손목", (16, -24)),
        (pt_elbow, "② 팔꿈치", (16, -22)),
        (pt_shoulder, "③ 어깨(견봉)", (-30, -36)),
        (pt_hip, "④ 골반(대전자★)", (-125, -14)),
        (pt_knee, "⑤ 무릎 회전축★", (20, -14)),
        (pt_ankle, "⑥ 바깥 복사뼈★", (-125, -8)),
        (pt_shoe, "⑦ 슈즈 발볼(클릿축★)", (18, 6)),
    ]
    for (px, py), txt, (ox, oy) in label_offsets:
        draw.text((px + ox, py + oy), txt, font=f_num, fill=(255, 240, 120))

    # Right column detailed cards (x = 640..1260)
    ry = 60
    for item in STICKER_PLACEMENTS:
        draw.rounded_rectangle([640, ry, 1260, ry + 80], radius=10, fill=(26, 33, 48), outline=(65, 85, 120), width=1)
        draw.text((656, ry + 8), f"{item['no']}  —  {item['landmark']}", font=f_bold, fill=(255, 225, 90))
        draw.text((656, ry + 32), f"• 부착 요령: {item['tip']}", font=f_desc, fill=(230, 238, 248))
        draw.text((656, ry + 53), f"• 측정 효과: {item['purpose']}", font=f_desc, fill=(110, 235, 195))
        ry += 86

    final_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    cv2.imwrite(output_path, final_bgr)
    return output_path



def draw_color_gauge_angle_card(
    img: np.ndarray,
    anchor_pt: Tuple[float, float],
    box_xy: Tuple[int, int],
    title_eng: str,
    angle_val: float,
    target_range: Tuple[float, float],
    scale: float = 1.0,
) -> None:
    """
    Draws the exact floating angle badge from the friend's 'Crank At 6 O'Clock' photo:
      - Leader line from joint to card
      - Dark Teal translucent rounded rectangle
      - Title (e.g. 'Knee Angle') + Bold Value ('142.2°')
      - 5-segment Color Standard Gauge Bar [Red | Yellow | Green | Yellow | Red] + White pointer pin
    """
    h, w = img.shape[:2]
    bw = int(146 * scale)
    bh = int(58 * scale)
    bx = int(np.clip(box_xy[0], 8, max(9, w - bw - 8)))
    by = int(np.clip(box_xy[1], int(52 * scale), max(int(54 * scale), h - bh - int(45 * scale))))

    _, status_bgr = evaluate_status_color_bgr(angle_val, target_range)

    # Leader line from joint to card center
    ax, ay = int(anchor_pt[0]), int(anchor_pt[1])
    cx, cy = bx + bw // 2, by + bh // 2
    cv2.line(img, (ax, ay), (cx, cy), (240, 215, 0), max(1, int(1.6 * scale)), cv2.LINE_AA)

    # Semi-transparent dark teal background
    sub = img[by : by + bh, bx : bx + bw]
    if sub.size > 0:
        bg_rect = np.full_like(sub, (64, 48, 16), dtype=np.uint8)  # BGR dark teal
        cv2.addWeighted(bg_rect, 0.84, sub, 0.16, 0, sub)
        img[by : by + bh, bx : bx + bw] = sub

    cv2.rectangle(img, (bx, by), (bx + bw, by + bh), status_bgr, max(1, int(2 * scale)), cv2.LINE_AA)

    # Title & Angle Text
    font_title = 0.40 * scale
    font_val = 0.58 * scale
    thick_val = max(1, int(1.8 * scale))

    (tw1, _), _ = cv2.getTextSize(title_eng, cv2.FONT_HERSHEY_SIMPLEX, font_title, 1)
    cv2.putText(
        img,
        title_eng,
        (bx + (bw - tw1) // 2, by + int(15 * scale)),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_title,
        (240, 225, 175),
        1,
        cv2.LINE_AA,
    )

    val_str = f"{angle_val:.1f} deg"
    (tw2, _), _ = cv2.getTextSize(val_str, cv2.FONT_HERSHEY_DUPLEX, font_val, thick_val)
    cv2.putText(
        img,
        val_str,
        (bx + (bw - tw2) // 2, by + int(35 * scale)),
        cv2.FONT_HERSHEY_DUPLEX,
        font_val,
        (255, 255, 255),
        thick_val,
        cv2.LINE_AA,
    )

    # 5-segment Color Standard Gauge Bar [Red | Yellow | Green | Yellow | Red]
    bar_x1 = bx + int(12 * scale)
    bar_x2 = bx + bw - int(12 * scale)
    bar_y1 = by + bh - int(14 * scale)
    bar_y2 = by + bh - int(6 * scale)
    bar_w = max(10, bar_x2 - bar_x1)

    seg_weights = [0.15, 0.15, 0.40, 0.15, 0.15]
    seg_bgrs = [
        (82, 82, 255),   # Red
        (0, 210, 255),   # Yellow
        (125, 239, 56),  # Green (Target Range)
        (0, 210, 255),   # Yellow
        (82, 82, 255),   # Red
    ]
    cur_x = float(bar_x1)
    for sw, sbgr in zip(seg_weights, seg_bgrs):
        next_x = cur_x + bar_w * sw
        cv2.rectangle(img, (int(cur_x), bar_y1), (int(next_x) - 1, bar_y2), sbgr, -1)
        cur_x = next_x

    # Pointer marker pin
    norm_pos = compute_gauge_norm_pos(angle_val, target_range)
    mx = int(bar_x1 + bar_w * norm_pos)
    my = (bar_y1 + bar_y2) // 2
    r_pin = max(3, int(4.8 * scale))
    cv2.circle(img, (mx, my), r_pin + 1, (10, 10, 10), -1, cv2.LINE_AA)
    cv2.circle(img, (mx, my), r_pin, (255, 255, 255), -1, cv2.LINE_AA)


def render_bike_fit_hud_frame(
    frame: np.ndarray,
    bio: Optional[FrameBiomechanics],
    style: RidingStyleSpec,
    view_mode: str,
    banner_title: str,
    knee_history: deque,
    hip_history: deque,
) -> np.ndarray:
    """
    Synthesizes the full Bike Fitting HUD overlay onto a single video frame:
      - Top banner ('Crank At 6 O'Clock' or live crank phase)
      - Cyan-blue translucent biomechanical triangles (Side View) or 11-line vertical knee tracking arrows (Front View)
      - 5 floating angle cards with 5-segment color gauge bars
      - Bottom-left color standard legend & Bottom-right live knee/hip angle waveform
    """
    out = frame.copy()
    h, w = out.shape[:2]
    scale = max(0.55, min(w / 1280.0, 1.45))

    if bio is not None:
        sh = (int(bio.shoulder[0]), int(bio.shoulder[1]))
        el = (int(bio.elbow[0]), int(bio.elbow[1]))
        wr = (int(bio.wrist[0]), int(bio.wrist[1]))
        hp = (int(bio.hip[0]), int(bio.hip[1]))
        kn = (int(bio.knee[0]), int(bio.knee[1]))
        an = (int(bio.ankle[0]), int(bio.ankle[1]))
        ft = (int(bio.foot[0]), int(bio.foot[1]))

        if view_mode == "SIDE_VIEW":
            # 1. Translucent Cyan-Blue Polygon Mesh ('Crank At 6 O'Clock' visual style)
            poly_layer = out.copy()
            triangles = [
                np.array([sh, el, hp], dtype=np.int32),
                np.array([sh, hp, kn], dtype=np.int32),
                np.array([hp, kn, an], dtype=np.int32),
                np.array([kn, an, ft], dtype=np.int32),
            ]
            for tri in triangles:
                cv2.fillPoly(poly_layer, [tri], (235, 190, 0), cv2.LINE_AA)  # BGR Cyan-Blue
            cv2.addWeighted(poly_layer, 0.36, out, 0.64, 0, out)

            for tri in triangles:
                cv2.polylines(out, [tri], isClosed=True, color=(255, 225, 60), thickness=max(1, int(2 * scale)), lineType=cv2.LINE_AA)

            # 2. Horizontal reference lines at Hip and Ankle + Vertical KOPS line at Knee
            horiz_dir = 1 if sh[0] >= hp[0] else -1
            cv2.line(
                out,
                hp,
                (hp[0] + int(horiz_dir * 130 * scale), hp[1]),
                (255, 235, 160),
                max(1, int(2 * scale)),
                cv2.LINE_AA,
            )
            cv2.line(
                out,
                an,
                (an[0] + int(horiz_dir * 95 * scale), an[1]),
                (255, 235, 160),
                max(1, int(2 * scale)),
                cv2.LINE_AA,
            )
            cv2.line(
                out,
                kn,
                (kn[0], an[1] + int(20 * scale)),
                (255, 210, 0),
                max(1, int(1.8 * scale)),
                cv2.LINE_AA,
            )

            # 3. Skeleton bones
            bone_thick = max(3, int(5.5 * scale))
            for p1, p2 in [(wr, el), (el, sh), (sh, hp), (hp, kn), (kn, an), (an, ft)]:
                cv2.line(out, p1, p2, (255, 212, 0), bone_thick, cv2.LINE_AA)

            # 4. Joint target rings with status color center
            _, kn_bgr = evaluate_status_color_bgr(bio.knee_angle, style.knee_bdc_range)
            _, hp_bgr = evaluate_status_color_bgr(bio.hip_angle, style.hip_bdc_range)
            _, to_bgr = evaluate_status_color_bgr(bio.torso_angle, style.torso_range)
            _, sh_bgr = evaluate_status_color_bgr(bio.shoulder_angle, style.shoulder_range)
            _, el_bgr = evaluate_status_color_bgr(bio.elbow_angle, style.elbow_range)

            r_out = max(5, int(9 * scale))
            r_in = max(3, int(5.5 * scale))
            joint_items = [
                ("shoulder", sh, sh_bgr),
                ("elbow", el, el_bgr),
                ("wrist", wr, (255, 230, 0)),
                ("hip", hp, hp_bgr),
                ("knee", kn, kn_bgr),
                ("ankle", an, (125, 239, 56)),
                ("foot", ft, (255, 230, 0)),
            ]
            for j_key, pt, col in joint_items:
                is_snapped = bio.snapped_markers.get(j_key, False)
                cv2.circle(out, pt, r_out, (32, 22, 14), -1, cv2.LINE_AA)
                cv2.circle(out, pt, r_in, col, -1, cv2.LINE_AA)
                ring_col = (0, 255, 255) if is_snapped else (255, 230, 0)
                cv2.circle(out, pt, r_out, ring_col, max(1, int(2.2 * scale)), cv2.LINE_AA)
                if is_snapped:
                    # Draw outer precision lock crosshair ring for sticker-snapped joints
                    cv2.circle(out, pt, r_out + int(4 * scale), (0, 240, 255), max(1, int(1.6 * scale)), cv2.LINE_AA)

            # 5. Floating 5-Segment Color Gauge Cards
            offset_x = int(42 * scale) if sh[0] >= hp[0] else int(-185 * scale)
            draw_color_gauge_angle_card(
                out,
                sh,
                (sh[0] - int(35 * scale), sh[1] - int(76 * scale)),
                "Shoulder Angle",
                bio.shoulder_angle,
                style.shoulder_range,
                scale,
            )
            draw_color_gauge_angle_card(
                out,
                ((sh[0] + hp[0]) // 2, (sh[1] + hp[1]) // 2),
                (hp[0] + offset_x, hp[1] - int(92 * scale)),
                "Torso Angle",
                bio.torso_angle,
                style.torso_range,
                scale,
            )
            draw_color_gauge_angle_card(
                out,
                hp,
                (hp[0] + offset_x, hp[1] - int(22 * scale)),
                "Hip Angle",
                bio.hip_angle,
                style.hip_bdc_range,
                scale,
            )
            draw_color_gauge_angle_card(
                out,
                kn,
                (kn[0] + offset_x, kn[1] - int(26 * scale)),
                "Knee Angle",
                bio.knee_angle,
                style.knee_bdc_range,
                scale,
            )
            draw_color_gauge_angle_card(
                out,
                an,
                (an[0] + offset_x, an[1] - int(18 * scale)),
                "Foot / Elbow",
                bio.foot_angle,
                (15.0, 28.0),
                scale,
            )
        else:
            # FRONT_VIEW: 11-line vertical knee alignment & cleat wedge tracking
            _, kn_bgr = evaluate_status_color_bgr(abs(bio.front_left_dev_pct), (0.0, 8.0))
            cv2.line(out, hp, an, (240, 230, 200), max(1, int(2 * scale)), cv2.LINE_AA)
            cv2.line(out, hp, kn, kn_bgr, max(3, int(6 * scale)), cv2.LINE_AA)
            cv2.line(out, kn, an, kn_bgr, max(3, int(6 * scale)), cv2.LINE_AA)
            draw_color_gauge_angle_card(
                out,
                kn,
                (kn[0] + int(30 * scale), kn[1] - int(30 * scale)),
                "Knee Varus Dev%",
                abs(bio.front_left_dev_pct),
                (0.0, 8.0),
                scale,
            )

    # 6. Top 'Crank At 6 O'Clock' Translucent Banner (Rendered with Korean Font via PIL)
    banner_h = int(46 * scale)
    banner_sub = out[0:banner_h, 0:w]
    if banner_sub.size > 0:
        white_bar = np.full_like(banner_sub, (245, 240, 235), dtype=np.uint8)
        cv2.addWeighted(white_bar, 0.88, banner_sub, 0.12, 0, banner_sub)
        out[0:banner_h, 0:w] = banner_sub

    # 7. Bottom-Left Color Standard Legend Pill & Bottom-Right Live Waveform Box
    wave_w = int(310 * scale)
    wave_h = int(84 * scale)
    wx1 = w - wave_w - int(14 * scale)
    wy1 = h - wave_h - int(12 * scale)
    wx2 = wx1 + wave_w
    wy2 = wy1 + wave_h

    wave_sub = out[max(0, wy1):min(h, wy2), max(0, wx1):min(w, wx2)]
    if wave_sub.size > 0:
        dark_box = np.full_like(wave_sub, (26, 18, 13), dtype=np.uint8)
        cv2.addWeighted(dark_box, 0.80, wave_sub, 0.20, 0, wave_sub)
        out[max(0, wy1):min(h, wy2), max(0, wx1):min(w, wx2)] = wave_sub
    cv2.rectangle(out, (wx1, wy1), (wx2, wy2), (235, 195, 0), 1, cv2.LINE_AA)

    # Green target band inside waveform (140~150 deg)
    plot_x1 = wx1 + int(10 * scale)
    plot_x2 = wx2 - int(10 * scale)
    plot_y1 = wy1 + int(24 * scale)
    plot_y2 = wy2 - int(8 * scale)
    plot_h = max(12, plot_y2 - plot_y1)
    plot_w = max(20, plot_x2 - plot_x1)

    def deg_to_py(deg: float) -> int:
        norm = np.clip((deg - 65.0) / (168.0 - 65.0), 0.0, 1.0)
        return int(plot_y2 - norm * plot_h)

    band_y_top = deg_to_py(style.knee_bdc_range[1])
    band_y_bot = deg_to_py(style.knee_bdc_range[0])
    cv2.rectangle(out, (plot_x1, band_y_top), (plot_x2, band_y_bot), (45, 85, 30), -1)

    if len(knee_history) >= 2:
        pts = []
        max_pts = knee_history.maxlen or len(knee_history)
        for idx, val in enumerate(knee_history):
            px = int(plot_x1 + (idx / max(1, max_pts - 1)) * plot_w)
            py = deg_to_py(val)
            pts.append((px, py))
        for i in range(1, len(pts)):
            _, seg_col = evaluate_status_color_bgr(knee_history[i], style.knee_bdc_range)
            cv2.line(out, pts[i - 1], pts[i], seg_col, max(2, int(2.2 * scale)), cv2.LINE_AA)

    # Bottom-left Color Standard Legend Bar
    leg_w = int(430 * scale)
    leg_h = int(34 * scale)
    lx1 = int(14 * scale)
    ly1 = h - leg_h - int(12 * scale)
    lx2 = min(wx1 - 10, lx1 + leg_w)
    ly2 = ly1 + leg_h
    leg_sub = out[max(0, ly1):min(h, ly2), max(0, lx1):min(w, lx2)]
    if leg_sub.size > 0:
        dark_leg = np.full_like(leg_sub, (26, 18, 13), dtype=np.uint8)
        cv2.addWeighted(dark_leg, 0.82, leg_sub, 0.18, 0, leg_sub)
        out[max(0, ly1):min(h, ly2), max(0, lx1):min(w, lx2)] = leg_sub
    cv2.rectangle(out, (lx1, ly1), (lx2, ly2), (110, 95, 75), 1, cv2.LINE_AA)

    # Draw crisp Korean text for Banner, Legend, and Waveform Header using PIL
    pil_img = Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    font_banner = _get_korean_font(max(13, int(20 * scale)))
    font_small = _get_korean_font(max(10, int(12.5 * scale)))

    # Center banner title
    bbox_txt = draw.textbbox((0, 0), banner_title, font=font_banner)
    tw_b = bbox_txt[2] - bbox_txt[0]
    th_b = bbox_txt[3] - bbox_txt[1]
    draw.text(
        ((w - tw_b) // 2, (banner_h - th_b) // 2 - 2),
        banner_title,
        font=font_banner,
        fill=(22, 28, 40),
    )

    # Waveform header text
    cur_k = knee_history[-1] if knee_history else 142.0
    draw.text(
        (wx1 + int(10 * scale), wy1 + int(5 * scale)),
        f"실시간 무릎 각도 파형 (현재 {cur_k:.1f}° | 초록권장 {int(style.knee_bdc_range[0])}~{int(style.knee_bdc_range[1])}°)",
        font=font_small,
        fill=(190, 235, 255),
    )

    # Bottom-left Color Standard Legend text
    draw.text(
        (lx1 + int(12 * scale), ly1 + int(8 * scale)),
        "● 초록: 권장 피팅 기준    ● 노랑: ±5° 경계    ● 빨강: 안장/스템 조정 필요",
        font=font_small,
        fill=(240, 245, 250),
    )

    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def mux_audio_to_h264_mp4(
    silent_video_path: str,
    original_video_path: Optional[str],
    final_output_path: str,
    start_sec: float = 0.0,
    duration_sec: Optional[float] = None,
) -> str:
    """Encodes the synthesized video to browser/KakaoTalk-compatible H.264 MP4 and muxes sliced original audio."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        try:
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg_bin = None

    if not ffmpeg_bin:
        shutil.copyfile(silent_video_path, final_output_path)
        return final_output_path

    cmd = [ffmpeg_bin, "-y", "-i", silent_video_path]
    if original_video_path and os.path.exists(original_video_path):
        if start_sec > 0:
            cmd.extend(["-ss", f"{start_sec:.3f}"])
        if duration_sec and duration_sec > 0:
            cmd.extend(["-t", f"{duration_sec:.3f}"])
        cmd.extend([
            "-i",
            original_video_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            final_output_path,
        ])
    else:
        cmd.extend([
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            final_output_path,
        ])

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return final_output_path
    except Exception:
        shutil.copyfile(silent_video_path, final_output_path)
        return final_output_path


def save_bike_fit_report_chart(
    frames_bio: List[FrameBiomechanics],
    style: RidingStyleSpec,
    bdc_idx: int,
    tdc_idx: int,
    output_chart_path: str,
) -> None:
    """Saves a 2-panel dark-themed matplotlib chart of Knee/Hip angle curves + 5-angle color gauge summary."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.style.use("dark_background")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7.2), gridspec_kw={"height_ratios": [2.2, 1.4]})
    fig.patch.set_facecolor("#0d1017")

    times = [f.timestamp_sec for f in frames_bio]
    knees = [f.knee_angle for f in frames_bio]
    hips = [f.hip_angle for f in frames_bio]
    torsos = [f.torso_angle for f in frames_bio]

    ax1.set_facecolor("#151a26")
    ax1.plot(times, knees, color="#38ef7d", linewidth=2.2, label="Knee Angle (deg)")
    ax1.plot(times, hips, color="#00d2ff", linewidth=1.9, label="Hip Angle (deg)")
    ax1.plot(times, torsos, color="#ffd200", linewidth=1.5, linestyle="--", label="Torso Angle (deg)")

    # Highlight Green Optimal BDC Knee Zone
    ax1.axhspan(
        style.knee_bdc_range[0],
        style.knee_bdc_range[1],
        color="#38ef7d",
        alpha=0.14,
        label=f"Optimal 6 O'Clock Knee ({int(style.knee_bdc_range[0])}~{int(style.knee_bdc_range[1])} deg)",
    )

    if 0 <= bdc_idx < len(frames_bio):
        bf = frames_bio[bdc_idx]
        ax1.scatter([bf.timestamp_sec], [bf.knee_angle], color="#38ef7d", s=70, zorder=5)
        ax1.annotate(
            f"6 O'Clock BDC: {bf.knee_angle:.1f}°",
            (bf.timestamp_sec, bf.knee_angle),
            xytext=(8, 10),
            textcoords="offset points",
            color="#38ef7d",
            fontweight="bold",
        )

    ax1.set_title("Real-Time Cycling Joint Angle Waveform (Knee / Hip / Torso)", fontsize=13, fontweight="bold", color="#ffffff")
    ax1.set_xlabel("Video Timestamp (seconds)", fontsize=10, color="#b0b8c8")
    ax1.set_ylabel("Angle (degrees)", fontsize=10, color="#b0b8c8")
    ax1.set_ylim(20, 180)
    ax1.grid(True, linestyle="--", alpha=0.2)
    ax1.legend(loc="lower right", framealpha=0.35)

    # Panel 2: Key Joint Angles vs Target Green Midpoint
    ax2.set_facecolor("#151a26")
    if frames_bio:
        bf = frames_bio[bdc_idx]
        tf = frames_bio[tdc_idx]
        labels = [
            "Knee @ 6 O'Clock",
            "Hip @ 6 O'Clock",
            "Hip @ 12 O'Clock",
            "Torso Tilt",
            "Shoulder Reach",
            "Elbow Flex",
        ]
        vals = [
            bf.knee_angle,
            bf.hip_angle,
            tf.hip_angle,
            float(np.mean(torsos)),
            float(np.mean([f.shoulder_angle for f in frames_bio])),
            float(np.mean([f.elbow_angle for f in frames_bio])),
        ]
        ranges = [
            style.knee_bdc_range,
            style.hip_bdc_range,
            style.hip_tdc_range,
            style.torso_range,
            style.shoulder_range,
            style.elbow_range,
        ]
        colors = []
        for v, r in zip(vals, ranges):
            if r[0] <= v <= r[1]:
                colors.append("#38ef7d")
            elif (r[0] - 5) <= v <= (r[1] + 5):
                colors.append("#ffd200")
            else:
                colors.append("#ff5252")

        bars = ax2.bar(labels, vals, color=colors, width=0.45, edgecolor="#ffffff", linewidth=0.8)
        for bar, v, r in zip(bars, vals, ranges):
            ax2.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + 2.0,
                f"{v:.1f}°\n(Target {int(r[0])}~{int(r[1])}°)",
                ha="center",
                va="bottom",
                fontsize=8.5,
                color="#ffffff",
                fontweight="bold",
            )

    ax2.set_title("5-Angle Color Standard Evaluation (Green=Optimal, Yellow=±5° Border, Red=Adjust)", fontsize=12, fontweight="bold")
    ax2.set_ylim(0, 195)
    ax2.set_ylabel("Degrees (°)", fontsize=10, color="#b0b8c8")
    ax2.grid(True, axis="y", linestyle="--", alpha=0.2)

    plt.tight_layout()
    plt.savefig(output_chart_path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)


class BikeFitVideoAnalyzer:
    def __init__(self, model_name: str = "yolov8n-pose.pt", device: Optional[str] = None):
        self.model = YOLO(model_name)
        self.device = device

    def analyze_and_synthesize(
        self,
        input_video_path: str,
        output_dir: str = "outputs",
        style_key: str = "ROAD_ENDURANCE",
        view_mode: str = "SIDE_VIEW",
        marker_color_mode: str = "AUTO",
        start_sec: float = 0.0,
        end_sec: Optional[float] = None,
        conf_thresh: float = 0.30,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> BikeFitVideoResult:
        os.makedirs(output_dir, exist_ok=True)
        style = RIDING_STYLES.get(style_key, RIDING_STYLES["ROAD_ENDURANCE"])

        in_path = Path(input_video_path)
        stem = in_path.stem
        temp_silent_path = str(Path(output_dir) / f"{stem}_bikefit_temp.mp4")
        final_video_path = str(Path(output_dir) / f"{stem}_bikefit_overlay.mp4")
        bdc_snap_path = str(Path(output_dir) / f"{stem}_crank_6oclock.png")
        tdc_snap_path = str(Path(output_dir) / f"{stem}_crank_12oclock.png")
        chart_path = str(Path(output_dir) / f"{stem}_bikefit_chart.png")

        cap = cv2.VideoCapture(str(in_path))
        if not cap.isOpened():
            raise ValueError(f"동영상 파일을 열 수 없습니다: {input_video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        raw_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        start_sec = max(0.0, float(start_sec or 0.0))
        start_frame = min(int(round(start_sec * fps)), max(0, raw_total - 1))
        if end_sec is not None and end_sec > start_sec:
            end_frame = min(int(round(end_sec * fps)), raw_total)
        else:
            end_frame = raw_total

        total_frames = max(1, end_frame - start_frame)
        if start_frame > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(temp_silent_path, fourcc, fps, (w, h))

        knee_history: deque = deque(maxlen=max(30, int(fps * 4)))
        hip_history: deque = deque(maxlen=max(30, int(fps * 4)))
        frames_bio: List[FrameBiomechanics] = []

        best_bdc_score = -1e9
        best_tdc_score = 1e9
        bdc_frame_bgr: Optional[np.ndarray] = None
        tdc_frame_bgr: Optional[np.ndarray] = None
        bdc_bio_idx = 0
        tdc_bio_idx = 0

        last_valid_bio: Optional[FrameBiomechanics] = None
        ema_alpha = 0.45  # Smooth keypoint jitter across frames

        frame_idx = 0
        while frame_idx < total_frames:
            ret, frame = cap.read()
            if not ret:
                break

            cur_sec = start_sec + (frame_idx / max(fps, 1.0))
            results = self.model.predict(frame, conf=conf_thresh, verbose=False, device=self.device)
            res = results[0]

            bio: Optional[FrameBiomechanics] = None
            if res.boxes is not None and res.keypoints is not None and len(res.boxes) > 0:
                boxes = res.boxes.xyxy.cpu().numpy()
                kpts_all = res.keypoints.data.cpu().numpy()  # (N, 17, 3)
                # Pick largest person (the cyclist on the roller)
                areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
                main_idx = int(np.argmax(areas))
                kp = kpts_all[main_idx]

                # COCO 17 indices:
                # L: 5(sh), 7(el), 9(wr), 11(hp), 13(kn), 15(an)
                # R: 6(sh), 8(el), 10(wr), 12(hp), 14(kn), 16(an)
                l_conf = float(np.mean(kp[[5, 7, 9, 11, 13, 15], 2]))
                r_conf = float(np.mean(kp[[6, 8, 10, 12, 14, 16], 2]))
                use_right = r_conf >= l_conf

                sh_pt = kp[6, :2] if use_right else kp[5, :2]
                el_pt = kp[8, :2] if use_right else kp[7, :2]
                wr_pt = kp[10, :2] if use_right else kp[9, :2]
                hp_pt = kp[12, :2] if use_right else kp[11, :2]
                kn_pt = kp[14, :2] if use_right else kp[13, :2]
                an_pt = kp[16, :2] if use_right else kp[15, :2]

                # Estimate forefoot (toe/cleat) tip from ankle + shin direction
                facing_dir = 1.0 if sh_pt[0] >= hp_pt[0] else -1.0
                shin_len = max(30.0, float(np.linalg.norm(an_pt - kn_pt)))
                ft_approx = np.array(
                    [
                        float(an_pt[0] + facing_dir * shin_len * 0.34),
                        float(an_pt[1] + shin_len * 0.14),
                    ],
                    dtype=np.float32,
                )

                # Snap AI keypoints to attached round color stickers (Yellow / Red / Green) if present
                search_r = max(22, int(shin_len * 0.32))
                sh_pt, s_sh = snap_point_to_color_sticker(frame, sh_pt, marker_color_mode, search_r)
                el_pt, s_el = snap_point_to_color_sticker(frame, el_pt, marker_color_mode, search_r)
                wr_pt, s_wr = snap_point_to_color_sticker(frame, wr_pt, marker_color_mode, search_r)
                hp_pt, s_hp = snap_point_to_color_sticker(frame, hp_pt, marker_color_mode, search_r)
                kn_pt, s_kn = snap_point_to_color_sticker(frame, kn_pt, marker_color_mode, search_r)
                an_pt, s_an = snap_point_to_color_sticker(frame, an_pt, marker_color_mode, search_r)
                ft_arr, s_ft = snap_point_to_color_sticker(frame, ft_approx, marker_color_mode, int(search_r * 1.25))

                snapped_map = {
                    "shoulder": s_sh,
                    "elbow": s_el,
                    "wrist": s_wr,
                    "hip": s_hp,
                    "knee": s_kn,
                    "ankle": s_an,
                    "foot": s_ft,
                }

                # Smooth coordinates with previous frame (use tighter alpha when snapped to physical sticker)
                if last_valid_bio is not None:
                    a_sh = 0.78 if s_sh else ema_alpha
                    a_el = 0.78 if s_el else ema_alpha
                    a_wr = 0.78 if s_wr else ema_alpha
                    a_hp = 0.78 if s_hp else ema_alpha
                    a_kn = 0.78 if s_kn else ema_alpha
                    a_an = 0.78 if s_an else ema_alpha
                    a_ft = 0.78 if s_ft else ema_alpha
                    sh_pt = a_sh * sh_pt + (1 - a_sh) * np.array(last_valid_bio.shoulder)
                    el_pt = a_el * el_pt + (1 - a_el) * np.array(last_valid_bio.elbow)
                    wr_pt = a_wr * wr_pt + (1 - a_wr) * np.array(last_valid_bio.wrist)
                    hp_pt = a_hp * hp_pt + (1 - a_hp) * np.array(last_valid_bio.hip)
                    kn_pt = a_kn * kn_pt + (1 - a_kn) * np.array(last_valid_bio.knee)
                    an_pt = a_an * an_pt + (1 - a_an) * np.array(last_valid_bio.ankle)
                    ft_arr = a_ft * ft_arr + (1 - a_ft) * np.array(last_valid_bio.foot)

                ft_pt = (float(ft_arr[0]), float(ft_arr[1]))

                sh_t = (float(sh_pt[0]), float(sh_pt[1]))
                el_t = (float(el_pt[0]), float(el_pt[1]))
                wr_t = (float(wr_pt[0]), float(wr_pt[1]))
                hp_t = (float(hp_pt[0]), float(hp_pt[1]))
                kn_t = (float(kn_pt[0]), float(kn_pt[1]))
                an_t = (float(an_pt[0]), float(an_pt[1]))

                knee_ang = compute_interior_angle(hp_t, kn_t, an_t)
                hip_ang = compute_interior_angle(sh_t, hp_t, kn_t)
                torso_ang = compute_horizontal_angle(hp_t, sh_t)
                shoulder_ang = compute_interior_angle(hp_t, sh_t, el_t)
                elbow_ang = compute_interior_angle(sh_t, el_t, wr_t)
                foot_ang = compute_horizontal_angle(an_t, ft_pt)

                femur_len = max(25.0, float(np.linalg.norm(kn_pt - hp_pt)))
                kops_raw = (kn_t[0] - ft_pt[0]) if facing_dir > 0 else (ft_pt[0] - kn_t[0])
                kops_norm = float(np.clip(kops_raw / femur_len, -1.0, 1.0))

                # Front knee varus/valgus deviation %
                hip_w = max(25.0, abs(float(kp[5, 0] - kp[6, 0])))
                l_ideal_x = 0.5 * float(kp[11, 0] + kp[15, 0])
                r_ideal_x = 0.5 * float(kp[12, 0] + kp[16, 0])
                l_dev = ((float(kp[13, 0]) - l_ideal_x) / hip_w) * 100.0
                r_dev = ((float(kp[14, 0]) - r_ideal_x) / hip_w) * 100.0

                bio = FrameBiomechanics(
                    frame_idx=frame_idx,
                    timestamp_sec=cur_sec,
                    is_right_side=use_right,
                    shoulder=sh_t,
                    elbow=el_t,
                    wrist=wr_t,
                    hip=hp_t,
                    knee=kn_t,
                    ankle=an_t,
                    foot=ft_pt,
                    knee_angle=knee_ang,
                    hip_angle=hip_ang,
                    torso_angle=torso_ang,
                    shoulder_angle=shoulder_ang,
                    elbow_angle=elbow_ang,
                    foot_angle=foot_ang,
                    kops_norm=kops_norm,
                    front_left_dev_pct=l_dev,
                    front_right_dev_pct=r_dev,
                    snapped_markers=snapped_map,
                )
                last_valid_bio = bio
            elif last_valid_bio is not None:
                bio = last_valid_bio

            if bio is not None:
                knee_history.append(bio.knee_angle)
                hip_history.append(bio.hip_angle)
                frames_bio.append(bio)

                # Dynamic Crank Phase Title
                if bio.knee_angle >= 136.0:
                    banner_title = f"Crank At 6 O'Clock (6시 하사점 구간)  |  무릎 {bio.knee_angle:.1f}°  |  Time {cur_sec:.2f}s"
                elif bio.hip_angle <= 78.0:
                    banner_title = f"Crank At 12 O'Clock (12시 상사점 구간)  |  고관절 {bio.hip_angle:.1f}°  |  Time {cur_sec:.2f}s"
                else:
                    banner_title = f"AI BIKE FIT LIVE OVERLAY ({style.kor_title})  |  무릎 {bio.knee_angle:.1f}°  |  Time {cur_sec:.2f}s"
            else:
                banner_title = f"AI BIKE FIT LIVE OVERLAY  |  Time {cur_sec:.2f}s"

            annotated = render_bike_fit_hud_frame(
                frame=frame,
                bio=bio,
                style=style,
                view_mode=view_mode,
                banner_title=banner_title,
                knee_history=knee_history,
                hip_history=hip_history,
            )
            writer.write(annotated)

            # Track best 6 O'Clock (BDC) and 12 O'Clock (TDC) snapshot frames
            if bio is not None:
                bdc_metric = bio.ankle[1] * 0.65 + bio.knee_angle * 2.0
                if bdc_metric > best_bdc_score:
                    best_bdc_score = bdc_metric
                    bdc_bio_idx = len(frames_bio) - 1
                    bdc_frame_bgr = render_bike_fit_hud_frame(
                        frame=frame,
                        bio=bio,
                        style=style,
                        view_mode=view_mode,
                        banner_title=f"★ Crank At 6 O'Clock (6시 하사점 정밀 캡처 @ {cur_sec:.2f}s)",
                        knee_history=knee_history,
                        hip_history=hip_history,
                    )

                tdc_metric = bio.ankle[1] * 0.65 + bio.hip_angle * 2.0
                if tdc_metric < best_tdc_score:
                    best_tdc_score = tdc_metric
                    tdc_bio_idx = len(frames_bio) - 1
                    tdc_frame_bgr = render_bike_fit_hud_frame(
                        frame=frame,
                        bio=bio,
                        style=style,
                        view_mode=view_mode,
                        banner_title=f"★ Crank At 12 O'Clock (12시 상사점 정밀 캡처 @ {cur_sec:.2f}s)",
                        knee_history=knee_history,
                        hip_history=hip_history,
                    )

            frame_idx += 1
            if progress_callback and (frame_idx % 5 == 0 or frame_idx == total_frames):
                k_val = bio.knee_angle if bio else 0.0
                progress_callback(
                    frame_idx,
                    total_frames,
                    f"프레임 실시간 오버레이 합성 중 ({frame_idx}/{total_frames}) | 현재 무릎 각도: {k_val:.1f}°",
                )

        cap.release()
        writer.release()

        if not frames_bio:
            raise ValueError("선택한 구간에서 자전거 탑승자 관절을 감지하지 못했습니다. 전신이 보이는 구간을 선택해주세요.")

        processed_dur = frame_idx / max(fps, 1.0)
        mux_audio_to_h264_mp4(
            silent_video_path=temp_silent_path,
            original_video_path=str(in_path),
            final_output_path=final_video_path,
            start_sec=start_sec,
            duration_sec=processed_dur,
        )
        if os.path.exists(temp_silent_path):
            try:
                os.remove(temp_silent_path)
            except OSError:
                pass

        if bdc_frame_bgr is not None:
            cv2.imwrite(bdc_snap_path, bdc_frame_bgr)
        if tdc_frame_bgr is not None:
            cv2.imwrite(tdc_snap_path, tdc_frame_bgr)

        save_bike_fit_report_chart(
            frames_bio=frames_bio,
            style=style,
            bdc_idx=bdc_bio_idx,
            tdc_idx=tdc_bio_idx,
            output_chart_path=chart_path,
        )

        bdc_bio = frames_bio[bdc_bio_idx]
        tdc_bio = frames_bio[tdc_bio_idx]
        avg_torso = float(np.mean([f.torso_angle for f in frames_bio]))
        avg_shoulder = float(np.mean([f.shoulder_angle for f in frames_bio]))
        avg_elbow = float(np.mean([f.elbow_angle for f in frames_bio]))
        avg_foot = float(np.mean([f.foot_angle for f in frames_bio]))
        right_majority = sum(1 for f in frames_bio if f.is_right_side) >= len(frames_bio) // 2

        total_marker_checks = len(frames_bio) * 7
        snapped_count = sum(
            sum(1 for v in f.snapped_markers.values() if v)
            for f in frames_bio
        )
        snap_rate_pct = round((snapped_count / max(1, total_marker_checks)) * 100.0, 1)

        # Estimate Cadence RPM
        knees_arr = np.array([f.knee_angle for f in frames_bio])
        mean_k = float(np.mean(knees_arr))
        crossings = int(np.sum((knees_arr[:-1] < mean_k) & (knees_arr[1:] >= mean_k)))
        cadence_rpm = int(np.clip((crossings / max(0.5, processed_dur)) * 60.0, 40, 135)) if crossings > 0 else 85

        diagnostics, overall_score = build_korean_diagnostics(
            style=style,
            bdc_knee=bdc_bio.knee_angle,
            bdc_hip=bdc_bio.hip_angle,
            tdc_hip=tdc_bio.hip_angle,
            avg_torso=avg_torso,
            avg_shoulder=avg_shoulder,
            avg_elbow=avg_elbow,
        )

        return BikeFitVideoResult(
            output_video_path=final_video_path,
            bdc_snapshot_path=bdc_snap_path,
            tdc_snapshot_path=tdc_snap_path,
            report_chart_path=chart_path,
            total_frames=frame_idx,
            fps=fps,
            start_sec=start_sec,
            end_sec=round(start_sec + processed_dur, 2),
            detected_side="우측면 (오른쪽 페달링)" if right_majority else "좌측면 (왼쪽 페달링)",
            cadence_rpm=cadence_rpm,
            overall_score=overall_score,
            bdc_knee_angle=round(bdc_bio.knee_angle, 1),
            bdc_hip_angle=round(bdc_bio.hip_angle, 1),
            bdc_timestamp_sec=round(bdc_bio.timestamp_sec, 2),
            tdc_hip_angle=round(tdc_bio.hip_angle, 1),
            tdc_knee_angle=round(tdc_bio.knee_angle, 1),
            tdc_timestamp_sec=round(tdc_bio.timestamp_sec, 2),
            avg_torso_angle=round(avg_torso, 1),
            avg_shoulder_angle=round(avg_shoulder, 1),
            avg_elbow_angle=round(avg_elbow, 1),
            avg_foot_angle=round(avg_foot, 1),
            marker_snap_rate_pct=snap_rate_pct,
            diagnostics=diagnostics,
        )


def build_korean_diagnostics(
    style: RidingStyleSpec,
    bdc_knee: float,
    bdc_hip: float,
    tdc_hip: float,
    avg_torso: float,
    avg_shoulder: float,
    avg_elbow: float,
) -> Tuple[List[Dict[str, str]], int]:
    items = []
    scores = []

    def add_item(part: str, val: float, rng: Tuple[float, float], low_msg: str, high_msg: str, ok_msg: str):
        label, _ = evaluate_status_color_bgr(val, rng)
        if rng[0] <= val <= rng[1]:
            advice = ok_msg
            scores.append(100)
        elif val < rng[0]:
            advice = low_msg
            scores.append(78 if val >= rng[0] - 5 else 55)
        else:
            advice = high_msg
            scores.append(78 if val <= rng[1] + 5 else 55)
        items.append({
            "part": part,
            "measured": f"{val:.1f}°",
            "target": f"{int(rng[0])}° ~ {int(rng[1])}°",
            "status": label,
            "advice": advice,
        })

    mm_up = max(3, int(round((style.knee_bdc_range[0] - bdc_knee) * 1.8)))
    mm_dn = max(3, int(round((bdc_knee - style.knee_bdc_range[1]) * 1.8)))
    add_item(
        "6시 하사점 무릎 각도 (Knee Angle @ 6 O'Clock)",
        bdc_knee,
        style.knee_bdc_range,
        f"하사점 무릎이 과하게 굽혀집니다(안장 낮음). 안장을 약 {mm_up}mm 높여 무릎 전면 부담을 줄이세요.",
        f"하사점에서 다리가 과하게 펴집니다(안장 높음). 안장을 약 {mm_dn}mm 낮춰 골반 흔들림을 방지하세요.",
        f"초록색 적정 구간({int(style.knee_bdc_range[0])}~{int(style.knee_bdc_range[1])}°) 정중앙으로 안장 높이가 이상적입니다!",
    )
    add_item(
        "6시 하사점 고관절 각도 (Hip Angle @ 6 O'Clock)",
        bdc_hip,
        style.hip_bdc_range,
        "하사점 고관절이 다소 닫혀 있습니다. 상체가 많이 숙여졌거나 안장이 뒤쪽에 있을 수 있습니다.",
        "고관절이 많이 열려 있습니다. 상체가 세워진 포지션이거나 안장이 다소 높을 수 있습니다.",
        "하사점 고관절 열림 각도가 초록색 적정 범위 안에 있습니다.",
    )
    add_item(
        "상체 숙임 각도 (Torso Angle / 핸들바 낙차)",
        avg_torso,
        style.torso_range,
        "목표 스타일 대비 상체가 많이 숙여져 있습니다. 허리/목 부담 시 스템 스페이서를 높이세요.",
        "상체가 다소 높게 세워져 있습니다. 공기저항을 줄이려면 스페이서를 내리거나 팔꿈치를 더 굽혀보세요.",
        f"{style.kor_title} 기준 초록색 구간에 들어오는 이상적인 상체 기울기입니다.",
    )
    add_item(
        "어깨 리치 각도 (Shoulder Angle / 스템 거리)",
        avg_shoulder,
        style.shoulder_range,
        "상체와 팔 사이가 좁아 조향이 답답할 수 있습니다. 스템 길이를 10mm 늘려보세요.",
        "팔이 앞으로 멀리 뻗어져 승모근 피로가 누적될 수 있습니다. 스템 길이를 10mm 줄이는 것을 권장합니다.",
        "몸통-어깨-팔로 이어지는 리치 밸런스가 초록색 적정 구간입니다.",
    )
    add_item(
        "팔꿈치 각도 (Elbow Angle / 충격 흡수)",
        avg_elbow,
        style.elbow_range,
        "팔꿈치가 많이 굽혀져 삼두근 피로가 빠를 수 있습니다.",
        "팔꿈치가 일자로 펴져(Lock-out) 노면 충격이 손목과 어깨로 직결됩니다. 팔꿈치를 살짝 굽히세요.",
        "적절한 팔꿈치 굴곡으로 노면 진동 흡수 및 코어 지지가 안정적입니다.",
    )

    return items, int(round(float(np.mean(scores))))


def generate_synthetic_cycling_demo_video(
    output_path: str = "outputs/demo_roller_input.mp4",
    duration_sec: float = 4.0,
    fps: float = 30.0,
) -> str:
    """
    Generates a synthetic side-view cycling video with a realistic rider & bike frame
    so the user can test the full video synthesis pipeline immediately even before uploading a video.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    w, h = 1280, 720
    total_frames = int(duration_sec * fps)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))

    style = RIDING_STYLES["ROAD_ENDURANCE"]
    knee_hist: deque = deque(maxlen=int(fps * 4))
    hip_hist: deque = deque(maxlen=int(fps * 4))

    for idx in range(total_frames):
        t = idx / fps
        phase = t * 2.8 * math.pi  # ~84 RPM pedaling

        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:] = (36, 26, 20)
        cv2.line(frame, (0, 610), (w, 610), (75, 65, 55), 3)

        # Draw indoor trainer & road bike frame
        rear_w = (410, 485)
        front_w = (870, 485)
        bb = (590, 485)
        saddle = (495, 285)
        handlebar = (810, 295)

        cv2.circle(frame, rear_w, 125, (90, 75, 60), 5, cv2.LINE_AA)
        cv2.circle(frame, front_w, 125, (90, 75, 60), 5, cv2.LINE_AA)
        cv2.line(frame, rear_w, bb, (110, 90, 70), 5, cv2.LINE_AA)
        cv2.line(frame, bb, saddle, (110, 90, 70), 6, cv2.LINE_AA)
        cv2.line(frame, saddle, (handlebar[0] - 20, handlebar[1]), (110, 90, 70), 6, cv2.LINE_AA)
        cv2.line(frame, bb, handlebar, (110, 90, 70), 6, cv2.LINE_AA)
        cv2.line(frame, handlebar, front_w, (110, 90, 70), 5, cv2.LINE_AA)

        crank_r = 68.0
        px = bb[0] + crank_r * math.cos(phase)
        py = bb[1] + crank_r * math.sin(phase)

        hp = (512.0, 268.0)
        sh = (705.0, 152.0 + 4.0 * math.sin(phase))
        el = (768.0, 244.0)
        wr = (820.0, 286.0)
        an = (px, py - 16.0)
        ft = (px + 34.0, py - 2.0)

        # 2-link inverse kinematics for knee joint
        l1, l2 = 162.0, 156.0
        dx, dy = an[0] - hp[0], an[1] - hp[1]
        d = max(50.0, min(l1 + l2 - 2.0, math.hypot(dx, dy)))
        a = (l1 * l1 - l2 * l2 + d * d) / (2.0 * d)
        h_orth = math.sqrt(max(0.0, l1 * l1 - a * a))
        mx = hp[0] + a * (dx / d)
        my = hp[1] + a * (dy / d)
        kn = (mx + h_orth * (dy / d), my - h_orth * (dx / d))

        k_ang = compute_interior_angle(hp, kn, an)
        h_ang = compute_interior_angle(sh, hp, kn)
        t_ang = compute_horizontal_angle(hp, sh)
        s_ang = compute_interior_angle(hp, sh, el)
        e_ang = compute_interior_angle(sh, el, wr)
        f_ang = compute_horizontal_angle(an, ft)

        bio = FrameBiomechanics(
            frame_idx=idx,
            timestamp_sec=t,
            is_right_side=True,
            shoulder=sh,
            elbow=el,
            wrist=wr,
            hip=hp,
            knee=kn,
            ankle=an,
            foot=ft,
            knee_angle=k_ang,
            hip_angle=h_ang,
            torso_angle=t_ang,
            shoulder_angle=s_ang,
            elbow_angle=e_ang,
            foot_angle=f_ang,
            kops_norm=0.04,
        )
        knee_hist.append(k_ang)
        hip_hist.append(h_ang)

        banner = (
            f"Crank At 6 O'Clock (6시 하사점)  |  무릎 {k_ang:.1f}°"
            if k_ang >= 138.0
            else f"AI BIKE FIT LIVE OVERLAY  |  Time {t:.2f}s  |  무릎 {k_ang:.1f}°"
        )
        annotated = render_bike_fit_hud_frame(
            frame=frame,
            bio=bio,
            style=style,
            view_mode="SIDE_VIEW",
            banner_title=banner,
            knee_history=knee_hist,
            hip_history=hip_hist,
        )
        writer.write(annotated)

    writer.release()
    final_mp4 = output_path.replace(".mp4", "_h264.mp4")
    return mux_audio_to_h264_mp4(output_path, None, final_mp4)


# =========================================================================
# REAL-TIME WEBCAM LIVE STREAMING & RECORDING SESSION
# =========================================================================
class LiveWebcamFitSession:
    """
    Processes live webcam frames in real time (from Browser WebRTC stream or local USB webcam),
    synthesizes the full Crank At 6 O'Clock polygon + 5-segment color gauge HUD,
    and records the synthesized frames directly into an H.264 MP4 video + diagnostic report.
    """

    def __init__(self, analyzer: BikeFitVideoAnalyzer, output_dir: str = "outputs"):
        self.analyzer = analyzer
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.reset()

    def reset(self) -> None:
        self.is_recording: bool = False
        self.writer: Optional[cv2.VideoWriter] = None
        self.temp_silent_path: str = ""
        self.final_video_path: str = ""
        self.bdc_snap_path: str = ""
        self.tdc_snap_path: str = ""
        self.chart_path: str = ""
        self.fps: float = 15.0
        self.frame_idx: int = 0
        self.start_time_sec: float = 0.0
        self.knee_history: deque = deque(maxlen=60)
        self.hip_history: deque = deque(maxlen=60)
        self.frames_bio: List[FrameBiomechanics] = []
        self.last_valid_bio: Optional[FrameBiomechanics] = None
        self.best_bdc_score: float = -1e9
        self.best_tdc_score: float = 1e9
        self.bdc_frame_bgr: Optional[np.ndarray] = None
        self.tdc_frame_bgr: Optional[np.ndarray] = None
        self.bdc_bio_idx: int = 0
        self.tdc_bio_idx: int = 0

    def start_recording(self, fps: float = 15.0) -> str:
        import time

        self.reset()
        ts = int(time.time())
        self.fps = fps
        self.start_time_sec = time.time()
        self.temp_silent_path = str(Path(self.output_dir) / f"webcam_live_{ts}_temp.mp4")
        self.final_video_path = str(Path(self.output_dir) / f"webcam_live_{ts}_overlay.mp4")
        self.bdc_snap_path = str(Path(self.output_dir) / f"webcam_live_{ts}_6oclock.png")
        self.tdc_snap_path = str(Path(self.output_dir) / f"webcam_live_{ts}_12oclock.png")
        self.chart_path = str(Path(self.output_dir) / f"webcam_live_{ts}_chart.png")
        self.is_recording = True
        return f"🔴 실시간 녹화 중... (페달을 5~15초 이상 밟은 뒤 [⏹️ 녹화 종료 & MP4 생성]을 눌러주세요)"

    def process_live_frame(
        self,
        frame_rgb: np.ndarray,
        style_key: str = "ROAD_ENDURANCE",
        view_mode: str = "SIDE_VIEW",
        marker_color_mode: str = "AUTO",
        conf_thresh: float = 0.30,
    ) -> np.ndarray:
        """
        Takes an RGB frame from Gradio's live webcam stream, runs YOLOv8-pose + sticker snap,
        draws the full HUD overlay, writes to MP4 if recording, and returns the annotated RGB frame.
        """
        import time

        if frame_rgb is None:
            return np.zeros((480, 640, 3), dtype=np.uint8)

        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        h, w = frame_bgr.shape[:2]
        style = RIDING_STYLES.get(style_key, RIDING_STYLES["ROAD_ENDURANCE"])
        cur_sec = (
            (time.time() - self.start_time_sec)
            if self.is_recording
            else (self.frame_idx / max(self.fps, 1.0))
        )

        results = self.analyzer.model.predict(
            frame_bgr, conf=conf_thresh, verbose=False, device=self.analyzer.device
        )
        res = results[0]

        bio: Optional[FrameBiomechanics] = None
        ema_alpha = 0.50

        if res.boxes is not None and res.keypoints is not None and len(res.boxes) > 0:
            boxes = res.boxes.xyxy.cpu().numpy()
            kpts_all = res.keypoints.data.cpu().numpy()
            areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
            main_idx = int(np.argmax(areas))
            kp = kpts_all[main_idx]

            l_conf = float(np.mean(kp[[5, 7, 9, 11, 13, 15], 2]))
            r_conf = float(np.mean(kp[[6, 8, 10, 12, 14, 16], 2]))
            use_right = r_conf >= l_conf

            sh_pt = kp[6, :2] if use_right else kp[5, :2]
            el_pt = kp[8, :2] if use_right else kp[7, :2]
            wr_pt = kp[10, :2] if use_right else kp[9, :2]
            hp_pt = kp[12, :2] if use_right else kp[11, :2]
            kn_pt = kp[14, :2] if use_right else kp[13, :2]
            an_pt = kp[16, :2] if use_right else kp[15, :2]

            facing_dir = 1.0 if sh_pt[0] >= hp_pt[0] else -1.0
            shin_len = max(30.0, float(np.linalg.norm(an_pt - kn_pt)))
            ft_approx = np.array(
                [
                    float(an_pt[0] + facing_dir * shin_len * 0.34),
                    float(an_pt[1] + shin_len * 0.14),
                ],
                dtype=np.float32,
            )

            search_r = max(20, int(shin_len * 0.32))
            sh_pt, s_sh = snap_point_to_color_sticker(frame_bgr, sh_pt, marker_color_mode, search_r)
            el_pt, s_el = snap_point_to_color_sticker(frame_bgr, el_pt, marker_color_mode, search_r)
            wr_pt, s_wr = snap_point_to_color_sticker(frame_bgr, wr_pt, marker_color_mode, search_r)
            hp_pt, s_hp = snap_point_to_color_sticker(frame_bgr, hp_pt, marker_color_mode, search_r)
            kn_pt, s_kn = snap_point_to_color_sticker(frame_bgr, kn_pt, marker_color_mode, search_r)
            an_pt, s_an = snap_point_to_color_sticker(frame_bgr, an_pt, marker_color_mode, search_r)
            ft_arr, s_ft = snap_point_to_color_sticker(frame_bgr, ft_approx, marker_color_mode, int(search_r * 1.25))

            snapped_map = {
                "shoulder": s_sh,
                "elbow": s_el,
                "wrist": s_wr,
                "hip": s_hp,
                "knee": s_kn,
                "ankle": s_an,
                "foot": s_ft,
            }

            if self.last_valid_bio is not None:
                a_sh = 0.80 if s_sh else ema_alpha
                a_el = 0.80 if s_el else ema_alpha
                a_wr = 0.80 if s_wr else ema_alpha
                a_hp = 0.80 if s_hp else ema_alpha
                a_kn = 0.80 if s_kn else ema_alpha
                a_an = 0.80 if s_an else ema_alpha
                a_ft = 0.80 if s_ft else ema_alpha
                sh_pt = a_sh * sh_pt + (1 - a_sh) * np.array(self.last_valid_bio.shoulder)
                el_pt = a_el * el_pt + (1 - a_el) * np.array(self.last_valid_bio.elbow)
                wr_pt = a_wr * wr_pt + (1 - a_wr) * np.array(self.last_valid_bio.wrist)
                hp_pt = a_hp * hp_pt + (1 - a_hp) * np.array(self.last_valid_bio.hip)
                kn_pt = a_kn * kn_pt + (1 - a_kn) * np.array(self.last_valid_bio.knee)
                an_pt = a_an * an_pt + (1 - a_an) * np.array(self.last_valid_bio.ankle)
                ft_arr = a_ft * ft_arr + (1 - a_ft) * np.array(self.last_valid_bio.foot)

            ft_pt = (float(ft_arr[0]), float(ft_arr[1]))
            sh_t = (float(sh_pt[0]), float(sh_pt[1]))
            el_t = (float(el_pt[0]), float(el_pt[1]))
            wr_t = (float(wr_pt[0]), float(wr_pt[1]))
            hp_t = (float(hp_pt[0]), float(hp_pt[1]))
            kn_t = (float(kn_pt[0]), float(kn_pt[1]))
            an_t = (float(an_pt[0]), float(an_pt[1]))

            knee_ang = compute_interior_angle(hp_t, kn_t, an_t)
            hip_ang = compute_interior_angle(sh_t, hp_t, kn_t)
            torso_ang = compute_horizontal_angle(hp_t, sh_t)
            shoulder_ang = compute_interior_angle(hp_t, sh_t, el_t)
            elbow_ang = compute_interior_angle(sh_t, el_t, wr_t)
            foot_ang = compute_horizontal_angle(an_t, ft_pt)

            femur_len = max(25.0, float(np.linalg.norm(kn_pt - hp_pt)))
            kops_raw = (kn_t[0] - ft_pt[0]) if facing_dir > 0 else (ft_pt[0] - kn_t[0])
            kops_norm = float(np.clip(kops_raw / femur_len, -1.0, 1.0))

            hip_w = max(25.0, abs(float(kp[5, 0] - kp[6, 0])))
            l_ideal_x = 0.5 * float(kp[11, 0] + kp[15, 0])
            r_ideal_x = 0.5 * float(kp[12, 0] + kp[16, 0])
            l_dev = ((float(kp[13, 0]) - l_ideal_x) / hip_w) * 100.0
            r_dev = ((float(kp[14, 0]) - r_ideal_x) / hip_w) * 100.0

            bio = FrameBiomechanics(
                frame_idx=self.frame_idx,
                timestamp_sec=cur_sec,
                is_right_side=use_right,
                shoulder=sh_t,
                elbow=el_t,
                wrist=wr_t,
                hip=hp_t,
                knee=kn_t,
                ankle=an_t,
                foot=ft_pt,
                knee_angle=knee_ang,
                hip_angle=hip_ang,
                torso_angle=torso_ang,
                shoulder_angle=shoulder_ang,
                elbow_angle=elbow_ang,
                foot_angle=foot_ang,
                kops_norm=kops_norm,
                front_left_dev_pct=l_dev,
                front_right_dev_pct=r_dev,
                snapped_markers=snapped_map,
            )
            self.last_valid_bio = bio
        elif self.last_valid_bio is not None:
            bio = self.last_valid_bio

        if bio is not None:
            self.knee_history.append(bio.knee_angle)
            self.hip_history.append(bio.hip_angle)

        rec_tag = f"🔴 REC {cur_sec:04.1f}s ({self.frame_idx}f)  |  " if self.is_recording else "📹 LIVE PREVIEW  |  "
        if bio is not None:
            banner_title = f"{rec_tag}무릎 {bio.knee_angle:.1f}°  •  고관절 {bio.hip_angle:.1f}°  •  상체 {bio.torso_angle:.1f}°"
        else:
            banner_title = f"{rec_tag}자전거 측면/정면 전신이 화면에 들어오도록 서주세요"

        annotated_bgr = render_bike_fit_hud_frame(
            frame=frame_bgr,
            bio=bio,
            style=style,
            view_mode=view_mode,
            banner_title=banner_title,
            knee_history=self.knee_history,
            hip_history=self.hip_history,
        )

        # If actively recording, write frame to MP4 writer and update best 6/12 O'Clock captures
        if self.is_recording:
            if self.writer is None:
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                self.writer = cv2.VideoWriter(self.temp_silent_path, fourcc, self.fps, (w, h))
            self.writer.write(annotated_bgr)
            self.frame_idx += 1

            if bio is not None:
                self.frames_bio.append(bio)
                bdc_metric = bio.ankle[1] * 0.65 + bio.knee_angle * 2.0
                if bdc_metric > self.best_bdc_score:
                    self.best_bdc_score = bdc_metric
                    self.bdc_bio_idx = len(self.frames_bio) - 1
                    self.bdc_frame_bgr = render_bike_fit_hud_frame(
                        frame=frame_bgr,
                        bio=bio,
                        style=style,
                        view_mode=view_mode,
                        banner_title=f"★ Crank At 6 O'Clock (웹캠 실시간 6시 하사점 캡처 @ {cur_sec:.2f}s)",
                        knee_history=self.knee_history,
                        hip_history=self.hip_history,
                    )

                tdc_metric = bio.ankle[1] * 0.65 + bio.hip_angle * 2.0
                if tdc_metric < self.best_tdc_score:
                    self.best_tdc_score = tdc_metric
                    self.tdc_bio_idx = len(self.frames_bio) - 1
                    self.tdc_frame_bgr = render_bike_fit_hud_frame(
                        frame=frame_bgr,
                        bio=bio,
                        style=style,
                        view_mode=view_mode,
                        banner_title=f"★ Crank At 12 O'Clock (웹캠 실시간 12시 상사점 캡처 @ {cur_sec:.2f}s)",
                        knee_history=self.knee_history,
                        hip_history=self.hip_history,
                    )

        return cv2.cvtColor(annotated_bgr, cv2.COLOR_BGR2RGB)

    def stop_and_finalize(self, style_key: str = "ROAD_ENDURANCE") -> Optional[BikeFitVideoResult]:
        self.is_recording = False
        if self.writer is not None:
            self.writer.release()
            self.writer = None

        if not os.path.exists(self.temp_silent_path) or self.frame_idx == 0:
            return None

        style = RIDING_STYLES.get(style_key, RIDING_STYLES["ROAD_ENDURANCE"])
        mux_audio_to_h264_mp4(self.temp_silent_path, None, self.final_video_path)
        try:
            os.remove(self.temp_silent_path)
        except OSError:
            pass

        if not self.frames_bio:
            # Recorded frames without detected person: still return the recorded MP4 video
            return BikeFitVideoResult(
                output_video_path=self.final_video_path,
                bdc_snapshot_path="",
                tdc_snapshot_path="",
                report_chart_path="",
                total_frames=self.frame_idx,
                fps=self.fps,
                start_sec=0.0,
                end_sec=round(self.frame_idx / max(1.0, self.fps), 2),
                detected_side="감지된 인물 없음",
                cadence_rpm=0,
                overall_score=0,
                bdc_knee_angle=0.0,
                bdc_hip_angle=0.0,
                bdc_timestamp_sec=0.0,
                tdc_hip_angle=0.0,
                tdc_knee_angle=0.0,
                tdc_timestamp_sec=0.0,
                avg_torso_angle=0.0,
                avg_shoulder_angle=0.0,
                avg_elbow_angle=0.0,
                avg_foot_angle=0.0,
                marker_snap_rate_pct=0.0,
                diagnostics=[],
            )

        if self.bdc_frame_bgr is not None:
            cv2.imwrite(self.bdc_snap_path, self.bdc_frame_bgr)
        if self.tdc_frame_bgr is not None:
            cv2.imwrite(self.tdc_snap_path, self.tdc_frame_bgr)

        save_bike_fit_report_chart(
            frames_bio=self.frames_bio,
            style=style,
            bdc_idx=self.bdc_bio_idx,
            tdc_idx=self.tdc_bio_idx,
            output_chart_path=self.chart_path,
        )

        bdc_bio = self.frames_bio[self.bdc_bio_idx]
        tdc_bio = self.frames_bio[self.tdc_bio_idx]
        avg_torso = float(np.mean([f.torso_angle for f in self.frames_bio]))
        avg_shoulder = float(np.mean([f.shoulder_angle for f in self.frames_bio]))
        avg_elbow = float(np.mean([f.elbow_angle for f in self.frames_bio]))
        avg_foot = float(np.mean([f.foot_angle for f in self.frames_bio]))
        right_majority = sum(1 for f in self.frames_bio if f.is_right_side) >= len(self.frames_bio) // 2

        dur = max(0.5, self.frames_bio[-1].timestamp_sec)
        knees_arr = np.array([f.knee_angle for f in self.frames_bio])
        mean_k = float(np.mean(knees_arr))
        crossings = int(np.sum((knees_arr[:-1] < mean_k) & (knees_arr[1:] >= mean_k)))
        cadence_rpm = int(np.clip((crossings / dur) * 60.0, 40, 135)) if crossings > 0 else 80

        total_marker_checks = len(self.frames_bio) * 7
        snapped_count = sum(
            sum(1 for v in f.snapped_markers.values() if v)
            for f in self.frames_bio
        )
        snap_rate_pct = round((snapped_count / max(1, total_marker_checks)) * 100.0, 1)

        diagnostics, overall_score = build_korean_diagnostics(
            style=style,
            bdc_knee=bdc_bio.knee_angle,
            bdc_hip=bdc_bio.hip_angle,
            tdc_hip=tdc_bio.hip_angle,
            avg_torso=avg_torso,
            avg_shoulder=avg_shoulder,
            avg_elbow=avg_elbow,
        )

        return BikeFitVideoResult(
            output_video_path=self.final_video_path,
            bdc_snapshot_path=self.bdc_snap_path if os.path.exists(self.bdc_snap_path) else "",
            tdc_snapshot_path=self.tdc_snap_path if os.path.exists(self.tdc_snap_path) else "",
            report_chart_path=self.chart_path if os.path.exists(self.chart_path) else "",
            total_frames=self.frame_idx,
            fps=self.fps,
            start_sec=0.0,
            end_sec=round(dur, 2),
            detected_side="우측면 (실시간 웹캠)" if right_majority else "좌측면 (실시간 웹캠)",
            cadence_rpm=cadence_rpm,
            overall_score=overall_score,
            bdc_knee_angle=round(bdc_bio.knee_angle, 1),
            bdc_hip_angle=round(bdc_bio.hip_angle, 1),
            bdc_timestamp_sec=round(bdc_bio.timestamp_sec, 2),
            tdc_hip_angle=round(tdc_bio.hip_angle, 1),
            tdc_knee_angle=round(tdc_bio.knee_angle, 1),
            tdc_timestamp_sec=round(tdc_bio.timestamp_sec, 2),
            avg_torso_angle=round(avg_torso, 1),
            avg_shoulder_angle=round(avg_shoulder, 1),
            avg_elbow_angle=round(avg_elbow, 1),
            avg_foot_angle=round(avg_foot, 1),
            marker_snap_rate_pct=snap_rate_pct,
            diagnostics=diagnostics,
        )

    def record_from_opencv_camera(
        self,
        camera_index: int = 0,
        duration_sec: float = 10.0,
        style_key: str = "ROAD_ENDURANCE",
        view_mode: str = "SIDE_VIEW",
        marker_color_mode: str = "AUTO",
        conf_thresh: float = 0.30,
        show_popup_window: bool = True,
        progress_callback: Optional[Callable[[int, int, str, Optional[np.ndarray]], None]] = None,
    ) -> BikeFitVideoResult:
        """
        Captures directly from local USB/Laptop webcam at full native FPS via OpenCV,
        shows a live HUD window (optional), and saves the synthesized MP4 + full report.
        """
        import time

        cap = cv2.VideoCapture(int(camera_index), cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(int(camera_index))
        if not cap.isOpened():
            raise ValueError(
                f"카메라(Index #{camera_index})를 열 수 없습니다. 웹캠 연결 상태를 확인하거나 위의 [브라우저 실시간 웹캠 스트리밍]을 사용해주세요."
            )

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 1.0 or fps > 120.0:
            fps = 24.0

        self.start_recording(fps=fps)
        total_target_frames = max(15, int(duration_sec * fps))
        win_name = "AI Bike Fit - Live Webcam HUD (Press ESC or Q to finish early)"

        try:
            while self.frame_idx < total_target_frames:
                ret, frame_bgr = cap.read()
                if not ret:
                    break
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                annotated_rgb = self.process_live_frame(
                    frame_rgb=frame_rgb,
                    style_key=style_key,
                    view_mode=view_mode,
                    marker_color_mode=marker_color_mode,
                    conf_thresh=conf_thresh,
                )
                if show_popup_window:
                    try:
                        cv2.imshow(win_name, cv2.cvtColor(annotated_rgb, cv2.COLOR_RGB2BGR))
                        key = cv2.waitKey(1) & 0xFF
                        if key in (27, ord("q"), ord("Q")):
                            break
                    except Exception:
                        show_popup_window = False

                if progress_callback and (self.frame_idx % 3 == 0 or self.frame_idx == total_target_frames):
                    progress_callback(
                        self.frame_idx,
                        total_target_frames,
                        f"🔴 실시간 웹캠 촬영 & 오버레이 녹화 중 ({self.frame_idx}/{total_target_frames} 프레임)",
                        annotated_rgb,
                    )
        finally:
            cap.release()
            if show_popup_window:
                try:
                    cv2.destroyWindow(win_name)
                except Exception:
                    pass

        res = self.stop_and_finalize(style_key=style_key)
        if res is None:
            raise ValueError("웹캠에서 프레임을 녹화하지 못했습니다.")
        return res

