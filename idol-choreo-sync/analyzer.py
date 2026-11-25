"""
Idol Choreography Synchronization Analyzer - Core Engine
--------------------------------------------------------
Uses YOLOv8-pose + ByteTrack for multi-person tracking and pose estimation,
computes scale/translation-invariant limb angles & bone vectors,
and synthesizes a rich HUD overlay onto the original video.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
from ultralytics import YOLO

# COCO 17 Keypoint indices:
# 0: nose, 1: left_eye, 2: right_eye, 3: left_ear, 4: right_ear
# 5: left_shoulder, 6: right_shoulder, 7: left_elbow, 8: right_elbow
# 9: left_wrist, 10: right_wrist, 11: left_hip, 12: right_hip
# 13: left_knee, 14: right_knee, 15: left_ankle, 16: right_ankle

# Limb vectors (start_kp, end_kp, name, weight)
LIMB_DEFINITIONS: List[Tuple[int, int, str, float]] = [
    (5, 7, "L_UpperArm", 1.3),
    (7, 9, "L_Forearm", 1.5),
    (6, 8, "R_UpperArm", 1.3),
    (8, 10, "R_Forearm", 1.5),
    (11, 13, "L_Thigh", 1.1),
    (13, 15, "L_Calf", 1.2),
    (12, 14, "R_Thigh", 1.1),
    (14, 16, "R_Calf", 1.2),
    (5, 6, "Shoulders", 0.8),
    (5, 11, "L_Torso", 0.8),
    (6, 12, "R_Torso", 0.8),
]

# Joint triplets for interior angles (kp_a, kp_vertex, kp_c, name)
JOINT_ANGLES: List[Tuple[int, int, int, str]] = [
    (5, 7, 9, "L_Elbow"),
    (6, 8, 10, "R_Elbow"),
    (11, 5, 7, "L_Shoulder"),
    (12, 6, 8, "R_Shoulder"),
    (11, 13, 15, "L_Knee"),
    (12, 14, 16, "R_Knee"),
    (5, 11, 13, "L_Hip"),
    (6, 12, 14, "R_Hip"),
]


@dataclass
class MemberFrameScore:
    track_id: int
    bbox: Tuple[int, int, int, int]
    keypoints: np.ndarray  # (17, 3) -> x, y, conf
    overall_score: float  # 0 ~ 100
    limb_scores: Dict[str, float] = field(default_factory=dict)  # limb_name -> 0~100


@dataclass
class AnalysisSummary:
    output_video_path: str
    report_image_path: str
    total_frames: int
    fps: float
    mean_sync_score: float
    max_sync_score: float
    min_sync_score: float
    best_timestamp_sec: float
    worst_timestamp_sec: float
    timeline_scores: List[float]
    member_avg_scores: Dict[int, float]


def compute_pose_features(
    kpts: np.ndarray, conf_thresh: float = 0.35
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Extracts scale-invariant unit direction vectors for limbs and interior joint angles.
    Returns:
        limb_vectors: (N_limbs, 2) unit vectors (dx, dy)
        limb_valid: (N_limbs,) boolean mask
        joint_angles: (N_angles,) angles in radians (NaN if invalid)
    """
    n_limbs = len(LIMB_DEFINITIONS)
    limb_vectors = np.zeros((n_limbs, 2), dtype=np.float32)
    limb_valid = np.zeros(n_limbs, dtype=bool)

    for idx, (i, j, _, _) in enumerate(LIMB_DEFINITIONS):
        if kpts[i, 2] >= conf_thresh and kpts[j, 2] >= conf_thresh:
            vec = kpts[j, :2] - kpts[i, :2]
            norm = np.linalg.norm(vec)
            if norm > 1e-3:
                limb_vectors[idx] = vec / norm
                limb_valid[idx] = True

    n_angles = len(JOINT_ANGLES)
    joint_angles = np.full(n_angles, np.nan, dtype=np.float32)
    for idx, (a, b, c, _) in enumerate(JOINT_ANGLES):
        if (
            kpts[a, 2] >= conf_thresh
            and kpts[b, 2] >= conf_thresh
            and kpts[c, 2] >= conf_thresh
        ):
            v1 = kpts[a, :2] - kpts[b, :2]
            v2 = kpts[c, :2] - kpts[b, :2]
            n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
            if n1 > 1e-3 and n2 > 1e-3:
                cos_angle = np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0)
                joint_angles[idx] = np.arccos(cos_angle)

    return limb_vectors, limb_valid, joint_angles


def evaluate_group_synchronization(
    detections: List[Tuple[int, Tuple[int, int, int, int], np.ndarray]],
    conf_thresh: float = 0.35,
) -> Tuple[Optional[float], List[MemberFrameScore]]:
    """
    Computes group choreography sync rate (0~100%) across all detected members in the current frame.
    Combines:
      1. Limb unit-vector directional agreement (handles 2D screen orientation)
      2. Interior joint angle agreement (handles slight perspective differences)
    """
    if len(detections) < 2:
        # Need at least 2 members to compute group synchronization
        single_results = []
        for tid, bbox, kpts in detections:
            single_results.append(
                MemberFrameScore(
                    track_id=tid,
                    bbox=bbox,
                    keypoints=kpts,
                    overall_score=100.0,
                    limb_scores={name: 100.0 for _, _, name, _ in LIMB_DEFINITIONS},
                )
            )
        return None, single_results

    n_members = len(detections)
    n_limbs = len(LIMB_DEFINITIONS)
    n_angles = len(JOINT_ANGLES)

    all_vecs = np.zeros((n_members, n_limbs, 2), dtype=np.float32)
    all_valid = np.zeros((n_members, n_limbs), dtype=bool)
    all_angles = np.full((n_members, n_angles), np.nan, dtype=np.float32)

    for m_idx, (_, _, kpts) in enumerate(detections):
        vecs, valid, angles = compute_pose_features(kpts, conf_thresh=conf_thresh)
        all_vecs[m_idx] = vecs
        all_valid[m_idx] = valid
        all_angles[m_idx] = angles

    # Compute mean Consensus Vector per limb across valid members
    consensus_vecs = np.zeros((n_limbs, 2), dtype=np.float32)
    limb_has_consensus = np.zeros(n_limbs, dtype=bool)

    for l_idx in range(n_limbs):
        valid_mask = all_valid[:, l_idx]
        if np.sum(valid_mask) >= 2:
            mean_v = np.mean(all_vecs[valid_mask, l_idx], axis=0)
            norm = np.linalg.norm(mean_v)
            if norm > 1e-4:
                consensus_vecs[l_idx] = mean_v / norm
                limb_has_consensus[l_idx] = True

    # Compute median Joint Angle per joint across valid members
    consensus_angles = np.full(n_angles, np.nan, dtype=np.float32)
    for a_idx in range(n_angles):
        valid_angles = all_angles[~np.isnan(all_angles[:, a_idx]), a_idx]
        if len(valid_angles) >= 2:
            consensus_angles[a_idx] = np.median(valid_angles)

    member_results: List[MemberFrameScore] = []
    weights = np.array([w for _, _, _, w in LIMB_DEFINITIONS], dtype=np.float32)

    for m_idx, (tid, bbox, kpts) in enumerate(detections):
        limb_scores: Dict[str, float] = {}
        weighted_sum = 0.0
        total_weight = 0.0

        for l_idx, (_, _, limb_name, w) in enumerate(LIMB_DEFINITIONS):
            if all_valid[m_idx, l_idx] and limb_has_consensus[l_idx]:
                # Cosine similarity between member limb vector and group consensus vector
                cos_sim = float(
                    np.clip(
                        np.dot(all_vecs[m_idx, l_idx], consensus_vecs[l_idx]),
                        -1.0,
                        1.0,
                    )
                )
                # Map angular difference to 0~100 score (0 deg diff -> 100, 90 deg diff -> 0)
                angle_diff_rad = np.arccos(cos_sim)
                score = max(0.0, 100.0 * (1.0 - (angle_diff_rad / (np.pi / 1.8))))
                limb_scores[limb_name] = score
                weighted_sum += score * w
                total_weight += w
            else:
                limb_scores[limb_name] = -1.0  # Not visible/valid

        # Incorporate interior joint angle similarity (25% boost to accuracy under perspective)
        angle_scores = []
        for a_idx in range(n_angles):
            if not np.isnan(all_angles[m_idx, a_idx]) and not np.isnan(
                consensus_angles[a_idx]
            ):
                diff = abs(all_angles[m_idx, a_idx] - consensus_angles[a_idx])
                a_score = max(0.0, 100.0 * (1.0 - diff / (np.pi / 2.0)))
                angle_scores.append(a_score)

        vec_overall = (weighted_sum / total_weight) if total_weight > 0 else 80.0
        if angle_scores:
            angle_overall = float(np.mean(angle_scores))
            overall_score = 0.7 * vec_overall + 0.3 * angle_overall
        else:
            overall_score = vec_overall

        member_results.append(
            MemberFrameScore(
                track_id=tid,
                bbox=bbox,
                keypoints=kpts,
                overall_score=float(np.clip(overall_score, 0.0, 100.0)),
                limb_scores=limb_scores,
            )
        )

    group_sync_score = float(np.mean([m.overall_score for m in member_results]))
    return group_sync_score, member_results


def score_to_bgr(score: float) -> Tuple[int, int, int]:
    """
    Maps a 0~100 sync score to a vivid BGR color:
      >= 85: Neon Green / Cyan-Green
      70~85: Yellow / Amber
      < 70:  Coral Red / Magenta-Red
    """
    if score < 0:
        return (160, 160, 160)  # Gray for occluded limb
    if score >= 85.0:
        # Interpolate 85..100 from Green (80, 230, 80) to Neon Cyan-Green (160, 255, 50)
        t = (score - 85.0) / 15.0
        b = int(80 + 80 * t)
        g = int(230 + 25 * t)
        r = int(80 - 30 * t)
        return (b, g, r)
    elif score >= 65.0:
        # Interpolate 65..85 from Orange-Yellow (30, 180, 255) to Green (80, 230, 80)
        t = (score - 65.0) / 20.0
        b = int(30 + 50 * t)
        g = int(180 + 50 * t)
        r = int(255 - 175 * t)
        return (b, g, r)
    else:
        # Below 65: Red-Pink (60, 60, 255)
        return (60, 60, 255)


def draw_hud_overlay(
    frame: np.ndarray,
    member_scores: List[MemberFrameScore],
    current_sync: float,
    history_scores: deque,
    frame_idx: int,
    total_frames: int,
    fps: float,
    conf_thresh: float = 0.35,
) -> np.ndarray:
    """
    Synthesizes the full choreography analysis HUD onto the video frame:
    - Semi-transparent top dashboard with Group Sync Gauge, Member Count, Timestamp
    - Per-member color-coded skeleton (green = synced, red = deviated) & floating ID badge
    - Bottom live scrolling waveform of synchronization rate over time
    """
    out = frame.copy()
    h, w = out.shape[:2]
    scale = max(0.5, min(w / 1280.0, 1.5))

    # 1. Draw per-member skeletons & badges
    overlay_skel = out.copy()
    for m in member_scores:
        kpts = m.keypoints
        x1, y1, x2, y2 = m.bbox
        member_color = score_to_bgr(m.overall_score)

        # Subtle corner bracket bounding box
        corner_len = max(10, int((x2 - x1) * 0.15))
        thick_box = max(1, int(2 * scale))
        # Top-left
        cv2.line(overlay_skel, (x1, y1), (x1 + corner_len, y1), member_color, thick_box, cv2.LINE_AA)
        cv2.line(overlay_skel, (x1, y1), (x1, y1 + corner_len), member_color, thick_box, cv2.LINE_AA)
        # Top-right
        cv2.line(overlay_skel, (x2, y1), (x2 - corner_len, y1), member_color, thick_box, cv2.LINE_AA)
        cv2.line(overlay_skel, (x2, y1), (x2, y1 + corner_len), member_color, thick_box, cv2.LINE_AA)
        # Bottom-left
        cv2.line(overlay_skel, (x1, y2), (x1 + corner_len, y2), member_color, thick_box, cv2.LINE_AA)
        cv2.line(overlay_skel, (x1, y2), (x1, y2 - corner_len), member_color, thick_box, cv2.LINE_AA)
        # Bottom-right
        cv2.line(overlay_skel, (x2, y2), (x2 - corner_len, y2), member_color, thick_box, cv2.LINE_AA)
        cv2.line(overlay_skel, (x2, y2), (x2, y2 - corner_len), member_color, thick_box, cv2.LINE_AA)

        # Draw limbs color-coded by individual limb accuracy
        limb_thick = max(2, int(4 * scale))
        for i, j, limb_name, _ in LIMB_DEFINITIONS:
            if kpts[i, 2] >= conf_thresh and kpts[j, 2] >= conf_thresh:
                pt1 = (int(kpts[i, 0]), int(kpts[i, 1]))
                pt2 = (int(kpts[j, 0]), int(kpts[j, 1]))
                l_score = m.limb_scores.get(limb_name, -1.0)
                l_color = score_to_bgr(l_score)
                cv2.line(overlay_skel, pt1, pt2, l_color, limb_thick, cv2.LINE_AA)

        # Draw keypoint joints
        joint_radius = max(3, int(5 * scale))
        for kp_idx in range(5, 17):  # Body joints (skip eyes/ears for cleaner look)
            if kpts[kp_idx, 2] >= conf_thresh:
                pt = (int(kpts[kp_idx, 0]), int(kpts[kp_idx, 1]))
                cv2.circle(overlay_skel, pt, joint_radius + 1, (20, 20, 20), -1, cv2.LINE_AA)
                cv2.circle(overlay_skel, pt, joint_radius, (255, 255, 255), -1, cv2.LINE_AA)

        # Floating badge above head/bbox
        badge_text = f"ID #{m.track_id} | {m.overall_score:.0f}%"
        font_scale = 0.52 * scale
        font_thick = max(1, int(1.6 * scale))
        (tw, th), _ = cv2.getTextSize(
            badge_text, cv2.FONT_HERSHEY_DUPLEX, font_scale, font_thick
        )
        bx1 = max(5, int((x1 + x2) / 2 - tw / 2 - 8))
        by1 = max(int(80 * scale), y1 - th - 14)
        bx2 = min(w - 5, bx1 + tw + 16)
        by2 = by1 + th + 10

        cv2.rectangle(overlay_skel, (bx1, by1), (bx2, by2), (18, 20, 28), -1)
        cv2.rectangle(overlay_skel, (bx1, by1), (bx2, by2), member_color, max(1, int(2 * scale)))
        cv2.putText(
            overlay_skel,
            badge_text,
            (bx1 + 8, by2 - 6),
            cv2.FONT_HERSHEY_DUPLEX,
            font_scale,
            (245, 245, 245),
            font_thick,
            cv2.LINE_AA,
        )

    cv2.addWeighted(overlay_skel, 0.88, out, 0.12, 0, out)

    # 2. Top HUD Panel (Semi-transparent dark glass bar)
    top_bar_h = int(76 * scale)
    hud_top = out.copy()
    cv2.rectangle(hud_top, (0, 0), (w, top_bar_h), (15, 17, 24), -1)
    cv2.addWeighted(hud_top, 0.78, out, 0.22, 0, out)
    cv2.line(out, (0, top_bar_h), (w, top_bar_h), (70, 80, 100), 1)

    # Title & Timestamp
    cur_sec = frame_idx / max(fps, 1.0)
    tot_sec = total_frames / max(fps, 1.0)
    title_text = "CHOREO SYNC ANALYZER"
    meta_text = f"DANCERS: {len(member_scores)}   TIME: {cur_sec:05.1f}s / {tot_sec:05.1f}s"

    cv2.putText(
        out,
        title_text,
        (int(20 * scale), int(30 * scale)),
        cv2.FONT_HERSHEY_DUPLEX,
        0.72 * scale,
        (255, 220, 100),
        max(1, int(2 * scale)),
        cv2.LINE_AA,
    )
    cv2.putText(
        out,
        meta_text,
        (int(20 * scale), int(58 * scale)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52 * scale,
        (200, 205, 215),
        max(1, int(1.4 * scale)),
        cv2.LINE_AA,
    )

    # Center-Right Sync Progress Gauge
    gauge_w = int(280 * scale)
    gauge_h = int(22 * scale)
    gx1 = w - gauge_w - int(190 * scale)
    gy1 = int(27 * scale)
    gx2 = gx1 + gauge_w
    gy2 = gy1 + gauge_h

    cv2.rectangle(out, (gx1, gy1), (gx2, gy2), (40, 44, 55), -1)
    fill_w = int(gauge_w * np.clip(current_sync / 100.0, 0.0, 1.0))
    sync_color = score_to_bgr(current_sync)
    if fill_w > 0:
        cv2.rectangle(out, (gx1, gy1), (gx1 + fill_w, gy2), sync_color, -1)
    cv2.rectangle(out, (gx1, gy1), (gx2, gy2), (160, 170, 185), 1)

    # Sync Rate Big Number on Top Right
    score_str = f"{current_sync:5.1f}%"
    cv2.putText(
        out,
        score_str,
        (gx2 + int(16 * scale), int(50 * scale)),
        cv2.FONT_HERSHEY_DUPLEX,
        1.05 * scale,
        sync_color,
        max(2, int(2.4 * scale)),
        cv2.LINE_AA,
    )

    # 3. Bottom Live Timeline Graph Box
    graph_h = int(84 * scale)
    graph_w = int(min(w - 40 * scale, 520 * scale))
    bx1 = int(20 * scale)
    by2 = h - int(16 * scale)
    by1 = by2 - graph_h
    bx2 = bx1 + graph_w

    hud_bot = out.copy()
    cv2.rectangle(hud_bot, (bx1, by1), (bx2, by2), (15, 17, 24), -1)
    cv2.addWeighted(hud_bot, 0.72, out, 0.28, 0, out)
    cv2.rectangle(out, (bx1, by1), (bx2, by2), (80, 90, 110), 1)

    cv2.putText(
        out,
        "LIVE SYNC TIMELINE (LAST 6s)",
        (bx1 + int(10 * scale), by1 + int(18 * scale)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42 * scale,
        (180, 190, 205),
        1,
        cv2.LINE_AA,
    )

    # Draw 80% reference line
    plot_top = by1 + int(24 * scale)
    plot_bot = by2 - int(8 * scale)
    plot_h = max(10, plot_bot - plot_top)
    y_80 = int(plot_bot - 0.80 * plot_h)
    cv2.line(out, (bx1 + 8, y_80), (bx2 - 8, y_80), (65, 85, 65), 1, cv2.LINE_AA)

    if len(history_scores) >= 2:
        pts = []
        max_pts = history_scores.maxlen or len(history_scores)
        inner_w = graph_w - 20
        for idx, val in enumerate(history_scores):
            px = int(bx1 + 10 + (idx / max(1, max_pts - 1)) * inner_w)
            py = int(plot_bot - (np.clip(val, 0.0, 100.0) / 100.0) * plot_h)
            pts.append((px, py))
        for i in range(1, len(pts)):
            seg_color = score_to_bgr(history_scores[i])
            cv2.line(out, pts[i - 1], pts[i], seg_color, max(2, int(2.2 * scale)), cv2.LINE_AA)

    return out


def save_summary_report_chart(
    timeline_scores: List[float],
    member_scores_accum: Dict[int, List[float]],
    fps: float,
    output_image_path: str,
    start_sec: float = 0.0,
) -> None:
    """Generates a high-resolution PNG analysis report with timeline curve and per-member rankings."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.style.use("dark_background")
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 7), gridspec_kw={"height_ratios": [2.2, 1.3]}
    )
    fig.patch.set_facecolor("#0f1118")

    # 1. Timeline Plot
    times = start_sec + (np.arange(len(timeline_scores)) / max(fps, 1.0))
    scores_arr = np.array(timeline_scores, dtype=np.float32)

    ax1.set_facecolor("#161924")
    ax1.plot(times, scores_arr, color="#38ef7d", linewidth=2.0, label="Group Sync Rate (%)")
    ax1.fill_between(times, scores_arr, 0, color="#38ef7d", alpha=0.15)
    ax1.axhline(85, color="#ffd200", linestyle="--", linewidth=1.0, alpha=0.7, label="Kal-Gunmu Threshold (85%)")

    if len(scores_arr) > 0:
        mean_val = float(np.mean(scores_arr))
        ax1.axhline(mean_val, color="#00d2ff", linestyle=":", linewidth=1.2, label=f"Average ({mean_val:.1f}%)")

    ax1.set_title("Choreography Synchronization Timeline", fontsize=14, fontweight="bold", pad=10, color="#ffffff")
    ax1.set_xlabel("Video Timestamp (seconds)", fontsize=10, color="#b0b8c8")
    ax1.set_ylabel("Sync Score (%)", fontsize=10, color="#b0b8c8")
    ax1.set_ylim(0, 105)
    ax1.grid(True, linestyle="--", alpha=0.2)
    ax1.legend(loc="lower right", framealpha=0.3)

    # 2. Per-Member Average Accuracy Bar Chart
    ax2.set_facecolor("#161924")
    filtered_members = {
        tid: float(np.mean(vals))
        for tid, vals in member_scores_accum.items()
        if len(vals) >= max(5, len(timeline_scores) // 10)
    }
    if not filtered_members and member_scores_accum:
        filtered_members = {tid: float(np.mean(vals)) for tid, vals in member_scores_accum.items()}

    if filtered_members:
        sorted_items = sorted(filtered_members.items(), key=lambda x: x[0])
        labels = [f"ID #{tid}" for tid, _ in sorted_items]
        avgs = [val for _, val in sorted_items]
        colors = ["#38ef7d" if v >= 85 else ("#ffb347" if v >= 70 else "#ff5e62") for v in avgs]

        bars = ax2.bar(labels, avgs, color=colors, width=0.45, edgecolor="#ffffff", linewidth=0.7)
        for bar, val in zip(bars, avgs):
            ax2.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + 1.5,
                f"{val:.1f}%",
                ha="center",
                va="bottom",
                fontsize=9,
                color="#ffffff",
                fontweight="bold",
            )
    ax2.set_title("Average Synchronization by Tracked Member ID", fontsize=12, fontweight="bold", pad=8)
    ax2.set_ylim(0, 110)
    ax2.set_ylabel("Avg Sync (%)", fontsize=10, color="#b0b8c8")
    ax2.grid(True, axis="y", linestyle="--", alpha=0.2)

    plt.tight_layout()
    plt.savefig(output_image_path, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)


def mux_original_audio(
    silent_video_path: str,
    original_video_path: str,
    final_output_path: str,
    start_sec: float = 0.0,
    duration_sec: Optional[float] = None,
) -> str:
    """
    Uses ffmpeg (if available on system PATH or via imageio-ffmpeg) to mux original audio
    from the matching [start_sec, start_sec + duration_sec] slice and ensure H.264 browser-compatible encoding.
    Falls back gracefully to the OpenCV MP4 if unavailable.
    """
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

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        silent_video_path,
    ]
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
        "22",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        final_output_path,
    ])
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return final_output_path
    except Exception:
        shutil.copyfile(silent_video_path, final_output_path)
        return final_output_path


class ChoreoSyncAnalyzer:
    def __init__(self, model_name: str = "yolov8n-pose.pt", device: Optional[str] = None):
        self.model = YOLO(model_name)
        self.device = device

    def analyze_video(
        self,
        input_video_path: str,
        output_dir: str = "outputs",
        conf_thresh: float = 0.35,
        min_box_area_ratio: float = 0.008,
        start_sec: float = 0.0,
        end_sec: Optional[float] = None,
        max_frames: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
    ) -> AnalysisSummary:
        """
        Runs full multi-person pose tracking + choreography synchronization analysis
        over the requested time range [start_sec, end_sec] and renders the composite HUD video + report chart.
        """
        os.makedirs(output_dir, exist_ok=True)
        in_path = Path(input_video_path)
        stem = in_path.stem

        temp_silent_path = str(Path(output_dir) / f"{stem}_temp_silent.mp4")
        final_video_path = str(Path(output_dir) / f"{stem}_choreo_sync.mp4")
        report_image_path = str(Path(output_dir) / f"{stem}_sync_report.png")

        cap = cv2.VideoCapture(str(in_path))
        if not cap.isOpened():
            raise ValueError(f"Cannot open video file: {input_video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        raw_total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        start_sec = max(0.0, float(start_sec or 0.0))
        start_frame = min(int(round(start_sec * fps)), max(0, raw_total_frames - 1))

        if end_sec is not None and end_sec > start_sec:
            end_frame = min(int(round(end_sec * fps)), raw_total_frames)
        else:
            end_frame = raw_total_frames

        total_frames = max(1, end_frame - start_frame)
        if max_frames and max_frames > 0:
            total_frames = min(total_frames, max_frames)

        if start_frame > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        # Reset tracker state before starting a new video/range
        if hasattr(self.model, "predictor") and self.model.predictor is not None:
            self.model.predictor.trackers = []

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(temp_silent_path, fourcc, fps, (width, height))

        frame_area = float(width * height)
        history_len = max(30, int(fps * 6))  # Last 6 seconds waveform
        history_scores: deque = deque(maxlen=history_len)
        timeline_scores: List[float] = []
        member_scores_accum: Dict[int, List[float]] = {}

        smoothed_sync = 88.0
        ema_alpha = 0.28  # Exponential moving average for smooth visual gauge

        frame_idx = 0
        while frame_idx < total_frames:
            ret, frame = cap.read()
            if not ret:
                break

            # Run YOLOv8-pose with ByteTrack persistence
            results = self.model.track(
                frame,
                persist=True,
                tracker="bytetrack.yaml",
                conf=conf_thresh,
                verbose=False,
                device=self.device,
            )

            detections: List[Tuple[int, Tuple[int, int, int, int], np.ndarray]] = []
            res = results[0]

            if (
                res.boxes is not None
                and res.keypoints is not None
                and len(res.boxes) > 0
            ):
                boxes_xyxy = res.boxes.xyxy.cpu().numpy()
                track_ids = (
                    res.boxes.id.int().cpu().tolist()
                    if res.boxes.id is not None
                    else list(range(1, len(boxes_xyxy) + 1))
                )
                kpts_data = res.keypoints.data.cpu().numpy()  # (N, 17, 3)

                for tid, box, kp in zip(track_ids, boxes_xyxy, kpts_data):
                    x1, y1, x2, y2 = map(int, box)
                    area = max(0, x2 - x1) * max(0, y2 - y1)
                    # Filter tiny background people/reflections
                    if area / frame_area >= min_box_area_ratio:
                        detections.append((int(tid), (x1, y1, x2, y2), kp))

            # Sort detections left-to-right for stable visual ordering
            detections.sort(key=lambda d: d[1][0])

            raw_sync, member_scores = evaluate_group_synchronization(
                detections, conf_thresh=conf_thresh
            )

            if raw_sync is not None:
                smoothed_sync = ema_alpha * raw_sync + (1.0 - ema_alpha) * smoothed_sync
            current_sync = float(np.clip(smoothed_sync, 0.0, 100.0))

            history_scores.append(current_sync)
            timeline_scores.append(current_sync)

            for m in member_scores:
                member_scores_accum.setdefault(m.track_id, []).append(m.overall_score)

            annotated_frame = draw_hud_overlay(
                frame=frame,
                member_scores=member_scores,
                current_sync=current_sync,
                history_scores=history_scores,
                frame_idx=start_frame + frame_idx,
                total_frames=start_frame + total_frames,
                fps=fps,
                conf_thresh=conf_thresh,
            )
            writer.write(annotated_frame)

            frame_idx += 1
            if progress_callback and (frame_idx % 5 == 0 or frame_idx == total_frames):
                progress_callback(frame_idx, total_frames, current_sync)

        cap.release()
        writer.release()

        processed_duration = frame_idx / max(fps, 1.0)
        # Mux original audio (sliced to start_sec..start_sec+processed_duration) & H.264 encode
        mux_original_audio(
            temp_silent_path,
            str(in_path),
            final_video_path,
            start_sec=start_sec,
            duration_sec=processed_duration,
        )
        if os.path.exists(temp_silent_path):
            try:
                os.remove(temp_silent_path)
            except OSError:
                pass

        # Save summary chart PNG
        save_summary_report_chart(
            timeline_scores=timeline_scores,
            member_scores_accum=member_scores_accum,
            fps=fps,
            output_image_path=report_image_path,
            start_sec=start_sec,
        )

        scores_arr = np.array(timeline_scores) if timeline_scores else np.array([0.0])
        best_idx = int(np.argmax(scores_arr))
        worst_idx = int(np.argmin(scores_arr))

        member_avg_scores = {
            tid: round(float(np.mean(vals)), 1)
            for tid, vals in member_scores_accum.items()
            if len(vals) >= max(5, len(timeline_scores) // 12)
        }

        return AnalysisSummary(
            output_video_path=final_video_path,
            report_image_path=report_image_path,
            total_frames=frame_idx,
            fps=fps,
            mean_sync_score=round(float(np.mean(scores_arr)), 2),
            max_sync_score=round(float(np.max(scores_arr)), 2),
            min_sync_score=round(float(np.min(scores_arr)), 2),
            best_timestamp_sec=round(start_sec + best_idx / max(fps, 1.0), 2),
            worst_timestamp_sec=round(start_sec + worst_idx / max(fps, 1.0), 2),
            timeline_scores=timeline_scores,
            member_avg_scores=member_avg_scores,
        )
