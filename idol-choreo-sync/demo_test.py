"""
Demo Video Test Harness
-----------------------
Generates or downloads a short multi-dancer test clip and runs the full HUD overlay pipeline
to verify end-to-end execution. Also supports testing with real dance videos.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np

from analyzer import (
    LIMB_DEFINITIONS,
    AnalysisSummary,
    ChoreoSyncAnalyzer,
    draw_hud_overlay,
    evaluate_group_synchronization,
    save_summary_report_chart,
)


def generate_synthetic_idol_group_session(
    output_dir: str = "outputs",
    num_members: int = 4,
    duration_sec: float = 6.0,
    fps: float = 30.0,
) -> AnalysisSummary:
    """
    Simulates a 4-member idol group choreography stage with realistic 17-keypoint skeletons,
    including synchronized sections ("Kal-Gunmu") and deliberate timing/angle errors by Member #3
    between 2.5s and 4.2s, rendering the exact HUD overlay video and report chart.
    """
    from collections import deque

    os.makedirs(output_dir, exist_ok=True)
    w, h = 1280, 720
    total_frames = int(duration_sec * fps)
    out_video_path = str(Path(output_dir) / "sample_idol_choreo_sync.mp4")
    report_img_path = str(Path(output_dir) / "sample_idol_sync_report.png")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_video_path, fourcc, fps, (w, h))

    history_scores: deque = deque(maxlen=int(fps * 6))
    timeline_scores: List[float] = []
    member_scores_accum = {tid: [] for tid in range(1, num_members + 1)}

    smoothed_sync = 92.0
    ema_alpha = 0.3

    # Base horizontal positions for 4 members on stage
    xs = [240, 500, 780, 1040]

    for f_idx in range(total_frames):
        t = f_idx / fps

        # Create stage background with studio floor grid & spotlight gradient
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        for y in range(h):
            val = int(18 + 22 * (y / h))
            frame[y, :] = (val + 8, val, val)
        # Studio floor line
        cv2.line(frame, (0, 560), (w, 560), (65, 65, 85), 2)

        detections: List[Tuple[int, Tuple[int, int, int, int], np.ndarray]] = []

        # Choreography beat phase
        beat = t * 2.5 * math.pi

        for m_idx in range(num_members):
            tid = m_idx + 1
            cx = int(xs[m_idx] + 18 * math.sin(beat * 0.5))
            cy_hip = int(390 + 12 * abs(math.sin(beat)))

            # Member #3 makes a choreography mistake between 2.2s and 4.0s
            phase_offset = 0.0
            arm_error = 0.0
            if tid == 3 and 2.2 <= t <= 4.0:
                phase_offset = 1.35
                arm_error = 1.2 * math.sin((t - 2.2) * math.pi / 1.8)
            elif tid == 2 and 4.3 <= t <= 5.0:
                arm_error = -0.65

            # Slight natural human variance
            micro_var = 0.05 * math.sin(beat + m_idx * 1.7)
            angle_l_arm = 0.85 * math.sin(beat + phase_offset) + arm_error + micro_var
            angle_r_arm = -0.85 * math.cos(beat + phase_offset) - arm_error + micro_var
            angle_leg = 0.25 * math.sin(beat * 0.5 + phase_offset) + micro_var * 0.5

            kpts = np.zeros((17, 3), dtype=np.float32)
            kpts[:, 2] = 0.95  # High confidence

            # Nose & Head
            kpts[0, :2] = [cx, cy_hip - 145]
            # Shoulders (5: L, 6: R)
            l_sh = np.array([cx - 34, cy_hip - 105], dtype=np.float32)
            r_sh = np.array([cx + 34, cy_hip - 105], dtype=np.float32)
            kpts[5, :2] = l_sh
            kpts[6, :2] = r_sh

            # Elbows (7: L, 8: R)
            l_elb = l_sh + np.array(
                [-52 * math.cos(angle_l_arm), -52 * math.sin(angle_l_arm)],
                dtype=np.float32,
            )
            r_elb = r_sh + np.array(
                [52 * math.cos(angle_r_arm), -52 * math.sin(angle_r_arm)],
                dtype=np.float32,
            )
            kpts[7, :2] = l_elb
            kpts[8, :2] = r_elb

            # Wrists (9: L, 10: R)
            l_wri = l_elb + np.array(
                [
                    -48 * math.cos(angle_l_arm + 0.55),
                    -48 * math.sin(angle_l_arm + 0.55),
                ],
                dtype=np.float32,
            )
            r_wri = r_elb + np.array(
                [
                    48 * math.cos(angle_r_arm - 0.55),
                    -48 * math.sin(angle_r_arm - 0.55),
                ],
                dtype=np.float32,
            )
            kpts[9, :2] = l_wri
            kpts[10, :2] = r_wri

            # Hips (11: L, 12: R)
            l_hip = np.array([cx - 24, cy_hip], dtype=np.float32)
            r_hip = np.array([cx + 24, cy_hip], dtype=np.float32)
            kpts[11, :2] = l_hip
            kpts[12, :2] = r_hip

            # Knees (13: L, 14: R)
            l_knee = l_hip + np.array(
                [-14 - 30 * math.sin(angle_leg), 78], dtype=np.float32
            )
            r_knee = r_hip + np.array(
                [14 + 30 * math.sin(angle_leg), 78], dtype=np.float32
            )
            kpts[13, :2] = l_knee
            kpts[14, :2] = r_knee

            # Ankles (15: L, 16: R)
            l_ank = l_knee + np.array([-8, 76], dtype=np.float32)
            r_ank = r_knee + np.array([8, 76], dtype=np.float32)
            kpts[15, :2] = l_ank
            kpts[16, :2] = r_ank

            # Draw subtle silhouette body on synthetic stage so it looks like dancers
            cv2.circle(frame, (int(cx), int(cy_hip - 145)), 22, (75, 70, 90), -1, cv2.LINE_AA)
            torso_pts = np.array([l_sh, r_sh, r_hip, l_hip], dtype=np.int32)
            cv2.fillPoly(frame, [torso_pts], (65, 60, 80))

            bbox = (cx - 95, cy_hip - 185, cx + 95, cy_hip + 170)
            detections.append((tid, bbox, kpts))

        raw_sync, member_scores = evaluate_group_synchronization(detections)
        if raw_sync is not None:
            smoothed_sync = ema_alpha * raw_sync + (1.0 - ema_alpha) * smoothed_sync
        current_sync = float(np.clip(smoothed_sync, 0.0, 100.0))

        history_scores.append(current_sync)
        timeline_scores.append(current_sync)
        for m in member_scores:
            member_scores_accum[m.track_id].append(m.overall_score)

        annotated = draw_hud_overlay(
            frame=frame,
            member_scores=member_scores,
            current_sync=current_sync,
            history_scores=history_scores,
            frame_idx=f_idx,
            total_frames=total_frames,
            fps=fps,
        )
        writer.write(annotated)

    writer.release()

    save_summary_report_chart(
        timeline_scores=timeline_scores,
        member_scores_accum=member_scores_accum,
        fps=fps,
        output_image_path=report_img_path,
    )

    scores_arr = np.array(timeline_scores)
    return AnalysisSummary(
        output_video_path=out_video_path,
        report_image_path=report_img_path,
        total_frames=total_frames,
        fps=fps,
        mean_sync_score=round(float(np.mean(scores_arr)), 2),
        max_sync_score=round(float(np.max(scores_arr)), 2),
        min_sync_score=round(float(np.min(scores_arr)), 2),
        best_timestamp_sec=round(int(np.argmax(scores_arr)) / fps, 2),
        worst_timestamp_sec=round(int(np.argmin(scores_arr)) / fps, 2),
        timeline_scores=timeline_scores,
        member_avg_scores={
            tid: round(float(np.mean(vals)), 1)
            for tid, vals in member_scores_accum.items()
        },
    )


if __name__ == "__main__":
    summary = generate_synthetic_idol_group_session()
    print("✅ Generated synthetic demo session:")
    print(f"   Video : {summary.output_video_path}")
    print(f"   Report: {summary.report_image_path}")
    print(f"   Mean Sync: {summary.mean_sync_score}% | Min: {summary.min_sync_score}% at {summary.worst_timestamp_sec}s")
