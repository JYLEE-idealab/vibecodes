"""
AI Bike Fitting Video Synthesizer & Live Webcam Recorder (Standalone Program)
-----------------------------------------------------------------------------
Features:
  - Tab 1: Upload recorded cycling video -> Set time range -> Synthesize Crank At 6 O'Clock
           cyan polygon + 5-segment color standard gauge bars [Red|Yellow|Green|Yellow|Red]
           + live angle waveform onto H.264 .MP4 video.
  - Tab 2: Real-Time Webcam Live Bike Fit & Auto Overlay MP4 Recorder
           (Supports both Browser WebRTC streaming & Direct OpenCV 720p high-FPS recording)
  - 7-Point Round Color Sticker Precision Snap Engine (±0.5° accuracy) & Anatomical Placement Guide
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from bike_fit_analyzer import (
    RIDING_STYLES,
    STICKER_PLACEMENTS,
    BikeFitVideoAnalyzer,
    LiveWebcamFitSession,
    generate_sticker_placement_guide_image,
    generate_synthetic_cycling_demo_video,
)


def launch_bike_fit_ui(default_model: str = "yolov8n-pose.pt", port: int = 7861) -> None:
    import cv2
    import gradio as gr

    bike_cache = {"name": default_model, "instance": BikeFitVideoAnalyzer(default_model)}
    live_session = LiveWebcamFitSession(bike_cache["instance"], output_dir="outputs")

    # Pre-generate demo synthesized cycling video and sticker placement diagram image
    demo_mp4_path = generate_synthetic_cycling_demo_video("outputs/demo_bikefit_synthesized.mp4")
    sticker_guide_img_path = generate_sticker_placement_guide_image("outputs/sticker_placement_guide.png")

    def on_video_upload(video_path: Optional[str]):
        if not video_path:
            return (
                gr.update(maximum=300.0, value=0.0),
                gr.update(maximum=300.0, value=0.0),
                "영상을 업로드하면 전체 길이와 해상도가 자동으로 표시됩니다.",
            )
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return (
                gr.update(maximum=300.0, value=0.0),
                gr.update(maximum=300.0, value=0.0),
                "⚠️ 영상 정보를 읽을 수 없습니다.",
            )
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()

        duration = round(max(1.0, frames / max(fps, 1.0)), 1)
        default_end = min(duration, 10.0)
        mins = int(duration // 60)
        secs = duration % 60
        info_msg = (
            f"🎬 **감지된 영상 정보:** 총 길이 `{duration:.1f}초` (`{mins}분 {secs:04.1f}초`) | "
            f"해상도 `{w}x{h}` | `{fps:.1f} FPS`"
        )
        return (
            gr.update(minimum=0.0, maximum=duration, value=0.0),
            gr.update(minimum=0.0, maximum=duration, value=default_end),
            info_msg,
        )

    def format_bike_fit_report_md(res, title_prefix: str = "AI 자전거 피팅 정밀 진단 리포트") -> str:
        if not res or not res.diagnostics:
            return "⚠️ 녹화된 구간에서 자전거 탑승자 관절이 충분히 감지되지 않았습니다. 카메라에 측면 또는 정면 전신이 나오도록 촬영해주세요."
        diag_rows = "\n".join(
            [
                f"| **{d['part']}** | **{d['measured']}** | `{d['target']}` | **{d['status']}** | {d['advice']} |"
                for d in res.diagnostics
            ]
        )
        return f"""
### 🚴 {title_prefix} (`{res.start_sec:.1f}초 ~ {res.end_sec:.1f}초` 구간 / 총 `{res.total_frames} 프레임`)
* **종합 피팅 점수:** **`{res.overall_score}점 / 100점`**  |  **감지 방향:** `{res.detected_side}`  |  **추정 케이던스:** `약 {res.cadence_rpm} RPM`  |  **🎯 컬러 스티커 마커 락온율:** `{res.marker_snap_rate_pct:.1f}%`
* **6시 하사점 (`Crank At 6 O'Clock` @ `{res.bdc_timestamp_sec:.2f}초`):** 무릎 각도 **`{res.bdc_knee_angle:.1f}°`** / 고관절 **`{res.bdc_hip_angle:.1f}°`**
* **12시 상사점 (`Crank At 12 O'Clock` @ `{res.tdc_timestamp_sec:.2f}초`):** 고관절 각도 **`{res.tdc_hip_angle:.1f}°`** / 무릎 **`{res.tdc_knee_angle:.1f}°`**

#### 🎨 부위별 색깔 기준 판정표 (`초록=권장 기준` / `노랑=±5° 경계` / `빨강=조정 필요`)
| 측정 부위 | 측정 각도 | 초록색 권장 기준 | 판정 상태 | 💡 안장 / 스템 / 클릿 피팅 처방전 |
| :--- | :---: | :---: | :---: | :--- |
{diag_rows}
"""

    def process_bike_fit_ui(
        video_path: str,
        start_sec: float,
        end_sec: float,
        style_choice: str,
        view_mode_choice: str,
        marker_color_choice: str,
        model_choice: str,
        conf_thresh: float,
        progress=gr.Progress(),
    ):
        if not video_path:
            raise gr.Error("분석할 로라 라이딩 동영상을 먼저 업로드해주세요!")

        start_val = max(0.0, float(start_sec or 0.0))
        end_val = float(end_sec) if (end_sec and end_sec > 0) else None
        if end_val is not None and end_val <= start_val:
            raise gr.Error("종료 시간은 시작 시간보다 뒤여야 합니다!")

        if bike_cache["name"] != model_choice:
            bike_cache["instance"] = BikeFitVideoAnalyzer(model_choice)
            bike_cache["name"] = model_choice
            live_session.analyzer = bike_cache["instance"]

        analyzer: BikeFitVideoAnalyzer = bike_cache["instance"]
        style_key = style_choice.split(" | ")[0].strip()
        view_key = "FRONT_VIEW" if "정면" in view_mode_choice else "SIDE_VIEW"
        marker_key = marker_color_choice.split(" | ")[0].strip()

        def ui_progress(cur: int, tot: int, msg: str):
            progress(cur / max(1, tot), desc=msg)

        res = analyzer.analyze_and_synthesize(
            input_video_path=video_path,
            output_dir="outputs",
            style_key=style_key,
            view_mode=view_key,
            marker_color_mode=marker_key,
            start_sec=start_val,
            end_sec=end_val,
            conf_thresh=conf_thresh,
            progress_callback=ui_progress,
        )

        return (
            res.output_video_path,
            res.bdc_snapshot_path,
            res.tdc_snapshot_path,
            res.report_chart_path,
            format_bike_fit_report_md(res),
        )

    def on_webcam_start_rec():
        return live_session.start_recording(fps=12.0)

    def on_webcam_stream_frame(frame_rgb, style_choice, view_choice, marker_choice):
        if frame_rgb is None:
            return None
        style_key = style_choice.split(" | ")[0].strip()
        view_key = "FRONT_VIEW" if "정면" in view_choice else "SIDE_VIEW"
        marker_key = marker_choice.split(" | ")[0].strip()
        return live_session.process_live_frame(
            frame_rgb=frame_rgb,
            style_key=style_key,
            view_mode=view_key,
            marker_color_mode=marker_key,
        )

    def on_webcam_stop_rec(style_choice):
        style_key = style_choice.split(" | ")[0].strip()
        res = live_session.stop_and_finalize(style_key=style_key)
        if res is None:
            raise gr.Error("먼저 [🔴 1. 실시간 녹화 시작] 버튼을 누르고 웹캠을 켠 상태에서 페달을 밟아주세요!")
        return (
            "✅ 녹화 완료! 아래에서 실시간 합성된 .MP4 동영상과 6시/12시 스냅샷 및 리포트를 확인하세요.",
            res.output_video_path,
            res.bdc_snapshot_path if res.bdc_snapshot_path else None,
            res.tdc_snapshot_path if res.tdc_snapshot_path else None,
            res.report_chart_path if res.report_chart_path else None,
            format_bike_fit_report_md(res, "실시간 웹캠 피팅 녹화 & 정밀 진단 리포트"),
        )

    def on_opencv_direct_record(
        cam_idx: int,
        rec_seconds: float,
        style_choice: str,
        view_choice: str,
        marker_choice: str,
        show_popup: bool,
        progress=gr.Progress(),
    ):
        style_key = style_choice.split(" | ")[0].strip()
        view_key = "FRONT_VIEW" if "정면" in view_choice else "SIDE_VIEW"
        marker_key = marker_choice.split(" | ")[0].strip()

        def cb(cur: int, tot: int, msg: str, _rgb):
            progress(cur / max(1, tot), desc=msg)

        res = live_session.record_from_opencv_camera(
            camera_index=int(cam_idx),
            duration_sec=float(rec_seconds),
            style_key=style_key,
            view_mode=view_key,
            marker_color_mode=marker_key,
            show_popup_window=bool(show_popup),
            progress_callback=cb,
        )
        return (
            f"✅ USB/노트북 웹캠 직접 녹화 완료! (`{res.output_video_path}`)",
            res.output_video_path,
            res.bdc_snapshot_path if res.bdc_snapshot_path else None,
            res.tdc_snapshot_path if res.tdc_snapshot_path else None,
            res.report_chart_path if res.report_chart_path else None,
            format_bike_fit_report_md(res, "USB/노트북 웹캠 직접 녹화 & 정밀 진단 리포트"),
        )

    style_choices = [f"{k} | {v.kor_title} ({v.subtitle})" for k, v in RIDING_STYLES.items()]
    marker_choices = [
        "AUTO | 🎯 자동 스티커 추적 (노랑·빨강·형광연두 원형 스티커 자동 스냅 + AI 하이브리드)",
        "YELLOW | 🟡 노란색 / 형광노랑 원형 스티커 전용 추적",
        "RED | 🔴 빨간색 / 주황색 원형 스티커 전용 추적",
        "GREEN | 🟢 초록색 / 형광연두 원형 스티커 전용 추적",
        "NONE | 🤖 스티커 없음 (순수 AI 관절 추정만 사용)",
    ]

    with gr.Blocks(title="AI Bike Fitting Studio") as demo:
        gr.Markdown(
            """
            # 🚴 AI 자전거 피팅 실시간 동영상 합성 & 웹캠 라이브 레코더
            **원본 로라 라이딩 영상 또는 실시간 웹캠 화면 위에 `Crank At 6 O'Clock` 블루 폴리곤, 5단계 색깔별 기준 게이지(`빨-노-초-노-빨`), 실시간 무릎 각도 파형 그래프를 합성하여 카카오톡으로 바로 공유 가능한 `.MP4` 동영상**을 생성합니다.
            """
        )

        with gr.Tabs():
            # =========================================================================
            # TAB 1: RECORDED VIDEO UPLOAD & MP4 OVERLAY SYNTHESIZER
            # =========================================================================
            with gr.TabItem("🎬 1. 녹화 동영상 업로드 & 피팅 오버레이 합성 (.MP4)"):
                gr.Markdown(
                    """
                    > 🎨 **색깔별 피팅 판정 기준:** 각 관절 카드 하단의 5칸 게이지 바에서 **🟢 초록색(중앙)** = 권장 피팅 기준 / **🟡 노란색** = ±5° 경계 / **🔴 빨간색** = 안장·스템 조정 필요
                    """
                )
                with gr.Row():
                    with gr.Column(scale=1):
                        bf_video_in = gr.Video(label="1️⃣ 로라 라이딩 원본 동영상 업로드 (MP4 / 측면 또는 정면)")
                        bf_info_md = gr.Markdown("영상을 업로드하면 전체 길이가 자동으로 표시됩니다.")

                        with gr.Group():
                            gr.Markdown("#### ⏱️ 2️⃣ 합성할 시간 구간(범위) & 라이딩 스타일 설정")
                            with gr.Row():
                                bf_start_slider = gr.Slider(
                                    minimum=0.0,
                                    maximum=300.0,
                                    value=0.0,
                                    step=0.5,
                                    label="시작 시간 (초)",
                                )
                                bf_end_slider = gr.Slider(
                                    minimum=0.0,
                                    maximum=300.0,
                                    value=10.0,
                                    step=0.5,
                                    label="종료 시간 (초, 페달링 5~15초 구간 권장)",
                                )
                            bf_style_dd = gr.Dropdown(
                                choices=style_choices,
                                value=style_choices[0],
                                label="🚲 목표 라이딩 포지션 (초록색 권장 각도 기준)",
                            )
                            bf_view_radio = gr.Radio(
                                choices=[
                                    "측면 피팅 모드 (Crank At 6 O'Clock 블루 폴리곤 & 5대 각도 컬러 게이지)",
                                    "정면 무릎 트래킹 모드 (골반-무릎-발목 11자 정렬 & 클릿 웨지 편차)",
                                ],
                                value="측면 피팅 모드 (Crank At 6 O'Clock 블루 폴리곤 & 5대 각도 컬러 게이지)",
                                label="📐 촬영 각도 모드",
                            )
                            bf_marker_dd = gr.Dropdown(
                                choices=marker_choices,
                                value=marker_choices[0],
                                label="🎯 관절·슈즈 원형 컬러 스티커 정밀 스냅 모드 (±0.5° 오차 감소)",
                            )

                        with gr.Accordion("📍 [필독] 오차를 ±0.5°로 줄이는 관절 & 슈즈 원형 스티커 7곳 부착 위치 가이드", open=True):
                            gr.Image(
                                value=sticker_guide_img_path,
                                label="관절 회전축 & 클릿 슈즈 스티커 7곳 부착 도해 (다이소 지름 1.5~2.5cm 노란색/빨간색 원형 견출지 권장)",
                                interactive=False,
                            )
                            gr.Markdown(
                                """
                                | 번호 | 부착 부위 | 정확한 해부학적 회전축 (뼈 위치) | 부착 요령 및 핵심 효과 |
                                | :---: | :--- | :--- | :--- |
                                | **①** | **손목 (Wrist)** | 손목 바깥쪽 복숭아뼈 (척골 경상돌기) | 장갑 위나 손목뼈 중앙에 부착 → **팔꿈치 굽힘각** 측정 |
                                | **②** | **팔꿈치 (Elbow)** | 팔꿈치 바깥쪽 볼록한 뼈 (상완골 외측상과) | 팔을 살짝 굽혔을 때 옆면 회전 중심 뼈 → **어깨 리치 & 팔꿈치 각도** |
                                | **③** | **어깨 (Shoulder)** | 어깨봉우리 뼈 (견봉, Acromion) 바로 아래 | 팔을 앞뒤로 흔들 때 중심축이 되는 어깨 옆면 → **상체 숙임각 & 어깨 각도** |
                                | **④★** | **골반 (Hip - 대전자)** | **고관절 대전자 (Greater Trochanter)** | **[가장 중요]** 다리를 들어 올릴 때 허벅지 옆에서 함께 구르는 큰 뼈 (허리 벨트라인 아님!) |
                                | **⑤★** | **무릎 (Knee)** | **대퇴골 외측상과 (무릎 옆면 회전축)** | 무릎 앞 뚜껑뼈(슬개골)가 아니라 옆면 회전 중심 → **6시 하사점 안장 높이 핵심 기준** |
                                | **⑥★** | **발목 (Ankle)** | **바깥쪽 복사뼈 정중앙 (외과)** | 양말/슈즈 위 복사뼈 가장 볼록한 정중앙 → **무릎 각도 & 앵클링 측정** |
                                | **⑦★** | **클릿 슈즈 앞코** | **제5중족골 헤드 (발볼 옆면 클릿 페달 중심축)** | 슈즈 보아 다이얼 아래 실제 페달 축이 지나는 옆면 → **발 각도(Foot Angle) & 3시 KOPS 100% 정밀 측정** |
                                """
                            )

                        with gr.Accordion("⚙️ 세부 AI 모델 설정", open=False):
                            bf_model_dd = gr.Dropdown(
                                choices=["yolov8n-pose.pt", "yolov8s-pose.pt", "yolov8m-pose.pt"],
                                value=default_model,
                                label="YOLOv8 Pose 모델 (n=빠름, s=균형, m=고정밀)",
                            )
                            bf_conf_slider = gr.Slider(
                                minimum=0.15,
                                maximum=0.70,
                                value=0.30,
                                step=0.05,
                                label="관절 감지 신뢰도 임계값",
                            )

                        bf_run_btn = gr.Button(
                            "🚀 선택한 구간 실시간 각도 폴리곤 + 컬러 게이지 동영상(.MP4) 합성 시작",
                            variant="primary",
                        )

                    with gr.Column(scale=1):
                        bf_video_out = gr.Video(
                            value=demo_mp4_path,
                            label="🎬 합성 완료된 피팅 동영상 (.MP4 다운로드 후 카톡으로 바로 전송 가능!)",
                        )
                        with gr.Row():
                            bf_bdc_img = gr.Image(label="🎯 자동 포착된 Crank At 6 O'Clock (6시 하사점) 스냅샷")
                            bf_tdc_img = gr.Image(label="⬆️ 자동 포착된 Crank At 12 O'Clock (12시 상사점) 스냅샷")
                        bf_chart_img = gr.Image(label="📈 시간대별 무릎/고관절/상체 각도 파형 & 색깔별 기준 리포트 차트")

                bf_report_md = gr.Markdown(
                    "👆 우측 상단 플레이어에 **실시간 합성 샘플 동영상**이 로드되어 있습니다. 좌측에 실제 로라 라이딩 영상을 올리고 **[합성 시작]** 버튼을 눌러보세요!"
                )

                bf_video_in.change(
                    fn=on_video_upload,
                    inputs=[bf_video_in],
                    outputs=[bf_start_slider, bf_end_slider, bf_info_md],
                )
                bf_run_btn.click(
                    fn=process_bike_fit_ui,
                    inputs=[
                        bf_video_in,
                        bf_start_slider,
                        bf_end_slider,
                        bf_style_dd,
                        bf_view_radio,
                        bf_marker_dd,
                        bf_model_dd,
                        bf_conf_slider,
                    ],
                    outputs=[bf_video_out, bf_bdc_img, bf_tdc_img, bf_chart_img, bf_report_md],
                )

            # =========================================================================
            # TAB 2: LIVE WEBCAM REAL-TIME BIKE FIT & MP4 RECORDER
            # =========================================================================
            with gr.TabItem("📹 2. 실시간 웹캠 라이브 피팅 & 자동 오버레이 녹화 (.MP4)"):
                gr.Markdown(
                    """
                    ### 📹 웹캠으로 로라 라이딩을 실시간 촬영하면서 각도 폴리곤 + 컬러 게이지를 바로 확인하고 `.MP4` 동영상으로 녹화합니다!
                    * **방식 A (브라우저 웹캠 스트리밍):** 좌측 웹캠 화면을 켜면 우측에 **실시간으로 각도 폴리곤과 5칸 컬러 게이지가 합성된 라이브 화면**이 나타납니다. **[🔴 1. 실시간 녹화 시작]** → 라이딩 → **[⏹️ 2. 녹화 종료 & MP4 생성]**을 누르면 즉시 합성 동영상이 완성됩니다.
                    * **방식 B (고성능 USB/노트북 카메라 직접 녹화):** 브라우저 제한 없이 720p 고프레임으로 지정한 시간(예: 10초) 동안 팝업 모니터 창을 띄워 실시간 합성 & 자동 녹화합니다.
                    """
                )
                with gr.Row():
                    wc_style_dd = gr.Dropdown(
                        choices=style_choices,
                        value=style_choices[0],
                        label="🚲 목표 라이딩 포지션 (초록색 권장 기준)",
                    )
                    wc_view_dd = gr.Dropdown(
                        choices=[
                            "측면 피팅 모드 (Crank At 6 O'Clock 블루 폴리곤 & 5대 각도 컬러 게이지)",
                            "정면 무릎 트래킹 모드 (골반-무릎-발목 11자 정렬 & 클릿 웨지 편차)",
                        ],
                        value="측면 피팅 모드 (Crank At 6 O'Clock 블루 폴리곤 & 5대 각도 컬러 게이지)",
                        label="📐 촬영 각도 모드",
                    )
                    wc_marker_dd = gr.Dropdown(
                        choices=marker_choices,
                        value=marker_choices[0],
                        label="🎯 원형 컬러 스티커 정밀 스냅 모드",
                    )

                wc_status_md = gr.Markdown("💡 **대기 중:** 아래에서 브라우저 웹캠을 켜거나, 우측의 **[USB/노트북 웹캠 고프레임 직접 촬영 & 녹화]**를 눌러주세요.")

                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("#### 🌐 방식 A: 브라우저 웹캠 실시간 스트리밍 & 수동 녹화")
                        with gr.Row():
                            wc_start_btn = gr.Button("🔴 1. 실시간 녹화 시작 (REC)", variant="primary")
                            wc_stop_btn = gr.Button("⏹️ 2. 녹화 종료 & 합성 MP4 동영상 생성", variant="stop")
                        with gr.Row():
                            wc_cam_in = gr.Image(
                                sources=["webcam"],
                                streaming=True,
                                type="numpy",
                                label="1️⃣ 내 웹캠 원본 입력 (카메라 아이콘 클릭)",
                            )
                            wc_live_out = gr.Image(
                                type="numpy",
                                label="2️⃣ 실시간 AI 각도 폴리곤 + 컬러 게이지 라이브 모니터",
                            )

                    with gr.Column(scale=1):
                        gr.Markdown("#### ⚡ 방식 B: PC 연결 USB/노트북 웹캠 720p 고프레임 자동 녹화 (추천)")
                        with gr.Group():
                            with gr.Row():
                                wc_cam_idx = gr.Number(value=0, precision=0, label="카메라 번호 (0=기본 웹캠, 1=외장 USB 웹캠)")
                                wc_rec_sec = gr.Slider(minimum=3, maximum=60, value=10, step=1, label="자동 녹화 시간 (초)")
                            wc_popup_chk = gr.Checkbox(
                                value=True,
                                label="촬영 중 PC 화면에 초저지연 실시간 피팅 모니터 창(OpenCV) 띄우기 (ESC 누르면 조기 종료)",
                            )
                            wc_direct_btn = gr.Button(
                                "🎬 USB/노트북 웹캠으로 지금 바로 실시간 촬영 + 오버레이 MP4 녹화 시작",
                                variant="primary",
                            )

                        wc_video_out = gr.Video(
                            label="🎬 실시간 웹캠 녹화 & 오버레이 합성 완료 동영상 (.MP4 다운로드 및 카톡 공유)"
                        )

                with gr.Row():
                    wc_bdc_img = gr.Image(label="🎯 녹화 중 자동 포착된 Crank At 6 O'Clock (6시 하사점) 스냅샷")
                    wc_tdc_img = gr.Image(label="⬆️ 녹화 중 자동 포착된 Crank At 12 O'Clock (12시 상사점) 스냅샷")
                wc_chart_img = gr.Image(label="📈 녹화 구간 실시간 무릎/고관절/상체 각도 파형 & 색깔별 기준 리포트 차트")
                wc_report_md = gr.Markdown("웹캠 녹화가 끝나면 이곳에 부위별 안장/스템 피팅 처방전이 자동 생성됩니다.")

                wc_cam_in.stream(
                    fn=on_webcam_stream_frame,
                    inputs=[wc_cam_in, wc_style_dd, wc_view_dd, wc_marker_dd],
                    outputs=[wc_live_out],
                    show_progress="hidden",
                )
                wc_start_btn.click(
                    fn=on_webcam_start_rec,
                    inputs=[],
                    outputs=[wc_status_md],
                )
                wc_stop_btn.click(
                    fn=on_webcam_stop_rec,
                    inputs=[wc_style_dd],
                    outputs=[wc_status_md, wc_video_out, wc_bdc_img, wc_tdc_img, wc_chart_img, wc_report_md],
                )
                wc_direct_btn.click(
                    fn=on_opencv_direct_record,
                    inputs=[wc_cam_idx, wc_rec_sec, wc_style_dd, wc_view_dd, wc_marker_dd, wc_popup_chk],
                    outputs=[wc_status_md, wc_video_out, wc_bdc_img, wc_tdc_img, wc_chart_img, wc_report_md],
                )

    demo.launch(
        server_port=port,
        inbrowser=False,
        theme=gr.themes.Soft(primary_hue="emerald", neutral_hue="slate"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Bike Fitting Video Synthesizer & Live Webcam Studio")
    parser.add_argument("--input", "-i", type=str, help="Path to input cycling video (.mp4)")
    parser.add_argument("--output", "-o", type=str, default="outputs", help="Output directory")
    parser.add_argument("--model", "-m", type=str, default="yolov8n-pose.pt", help="YOLOv8 pose model")
    parser.add_argument("--start", type=float, default=0.0, help="Start time in seconds")
    parser.add_argument("--end", type=float, default=None, help="End time in seconds")
    parser.add_argument("--port", type=int, default=7861, help="Web UI port (default: 7861)")
    parser.add_argument("--ui", action="store_true", help="Launch Gradio Web UI")
    args = parser.parse_args()

    if args.ui or not args.input:
        print(f"🌐 Launching AI Bike Fitting Studio Web UI on port {args.port}...")
        launch_bike_fit_ui(default_model=args.model, port=args.port)
    else:
        analyzer = BikeFitVideoAnalyzer(model_name=args.model)
        res = analyzer.analyze_and_synthesize(
            input_video_path=args.input,
            output_dir=args.output,
            start_sec=args.start,
            end_sec=args.end,
        )
        print(f"✅ Bike Fit Synthesized Video: {res.output_video_path}")


if __name__ == "__main__":
    main()
