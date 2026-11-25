"""
Idol Choreography Synchronization Video Synthesizer (Standalone Program)
------------------------------------------------------------------------
Usage:
  1. Launch Interactive Web UI:
     python main.py --ui

  2. Analyze a video directly via CLI:
     python main.py --input path/to/dance_practice.mp4 --start 15 --end 45 --output outputs/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from analyzer import ChoreoSyncAnalyzer


def run_cli(
    input_video: str,
    output_dir: str,
    model_name: str,
    conf_thresh: float,
    start_sec: float,
    end_sec: Optional[float],
    max_frames: Optional[int],
) -> None:
    print("=" * 65)
    print(" 🕺 IDOL CHOREOGRAPHY SYNCHRONIZATION ANALYZER (YOLOv8-Pose) ")
    print("=" * 65)
    print(f" • Input Video : {input_video}")
    print(f" • Model       : {model_name}")
    print(f" • Time Range  : {start_sec:.1f}s ~ {f'{end_sec:.1f}s' if end_sec else 'End'}")
    print(f" • Output Dir  : {output_dir}")
    print("-" * 65)

    analyzer = ChoreoSyncAnalyzer(model_name=model_name)

    def on_progress(cur_frame: int, tot_frames: int, cur_sync: float) -> None:
        pct = (cur_frame / max(1, tot_frames)) * 100.0
        sys.stdout.write(
            f"\r [Analyzing] Frame {cur_frame:4d}/{tot_frames:4d} ({pct:5.1f}%) | Live Sync: {cur_sync:5.1f}%"
        )
        sys.stdout.flush()

    summary = analyzer.analyze_video(
        input_video_path=input_video,
        output_dir=output_dir,
        conf_thresh=conf_thresh,
        start_sec=start_sec,
        end_sec=end_sec,
        max_frames=max_frames,
        progress_callback=on_progress,
    )
    print("\n" + "=" * 65)
    print(" ✅ ANALYSIS COMPLETE!")
    print("=" * 65)
    print(f" • Synthesized Video : {summary.output_video_path}")
    print(f" • Report Chart PNG  : {summary.report_image_path}")
    print(f" • Average Sync Rate : {summary.mean_sync_score:.1f}%")
    print(
        f" • Best Sync Moment  : {summary.max_sync_score:.1f}% (at {summary.best_timestamp_sec:.1f}s)"
    )
    print(
        f" • Lowest Sync Moment: {summary.min_sync_score:.1f}% (at {summary.worst_timestamp_sec:.1f}s)"
    )
    if summary.member_avg_scores:
        print(" • Per-Member Average Sync:")
        for tid, score in sorted(summary.member_avg_scores.items()):
            print(f"     - Member ID #{tid}: {score:.1f}%")
    print("=" * 65)


def launch_gradio_ui(default_model: str = "yolov8n-pose.pt", port: int = 7860) -> None:
    import cv2
    import gradio as gr

    analyzer_cache = {"name": default_model, "instance": ChoreoSyncAnalyzer(default_model)}

    def on_video_upload(video_path: Optional[str]):
        if not video_path:
            return (
                gr.update(maximum=300.0, value=0.0),
                gr.update(maximum=300.0, value=0.0),
                "영상을 업로드하면 전체 길이가 자동으로 표시됩니다.",
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
        mins = int(duration // 60)
        secs = duration % 60
        info_msg = f"🎬 **감지된 영상 정보:** 총 길이 `{duration:.1f}초` (`{mins}분 {secs:04.1f}초`) | 해상도 `{w}x{h}` | `{fps:.1f} FPS`"
        return (
            gr.update(minimum=0.0, maximum=duration, value=0.0),
            gr.update(minimum=0.0, maximum=duration, value=duration),
            info_msg,
        )

    def process_video_ui(
        video_path: str,
        start_sec: float,
        end_sec: float,
        model_choice: str,
        conf_thresh: float,
        progress=gr.Progress(),
    ):
        if not video_path:
            raise gr.Error("분석할 안무 동영상을 업로드해주세요!")

        start_val = max(0.0, float(start_sec or 0.0))
        end_val = float(end_sec) if (end_sec and end_sec > 0) else None
        if end_val is not None and end_val <= start_val:
            raise gr.Error("종료 시간(끝 구간)은 시작 시간보다 뒤여야 합니다!")

        if analyzer_cache["name"] != model_choice:
            analyzer_cache["instance"] = ChoreoSyncAnalyzer(model_choice)
            analyzer_cache["name"] = model_choice

        analyzer: ChoreoSyncAnalyzer = analyzer_cache["instance"]

        def ui_progress(cur: int, tot: int, sync_val: float):
            progress(
                cur / max(1, tot),
                desc=f"선택 구간 프레임 합성 중 ({cur}/{tot}) | 현재 군무 일치율: {sync_val:.1f}%",
            )

        summary = analyzer.analyze_video(
            input_video_path=video_path,
            output_dir="outputs",
            conf_thresh=conf_thresh,
            start_sec=start_val,
            end_sec=end_val,
            progress_callback=ui_progress,
        )

        actual_end = round(start_val + (summary.total_frames / max(summary.fps, 1.0)), 1)

        member_lines = "\n".join(
            [
                f"- **멤버 ID #{tid}**: 평균 `{score:.1f}%`"
                for tid, score in sorted(summary.member_avg_scores.items())
            ]
        )
        if not member_lines:
            member_lines = "- 감지된 다중 멤버 정보가 부족합니다 (2명 이상 필요)"

        md_report = f"""
### 📊 칼군무 분석 요약 리포트 (`{start_val:.1f}초 ~ {actual_end:.1f}초` 구간)
| 항목 | 결과 |
| :--- | :--- |
| **분석 구간** | `{start_val:.1f}초` ~ `{actual_end:.1f}초` (총 `{actual_end - start_val:.1f}초` 분석) |
| **구간 평균 칼군무 일치율** | **{summary.mean_sync_score:.1f}%** |
| **최고 싱크로율 순간 (하이라이트)** | **{summary.max_sync_score:.1f}%** (원본 `{summary.best_timestamp_sec:.1f}초` 지점) |
| **최저 싱크로율 순간 (안무 오차 주의)** | **{summary.min_sync_score:.1f}%** (원본 `{summary.worst_timestamp_sec:.1f}초` 지점) |
| **총 분석 프레임 / FPS** | `{summary.total_frames} frames` (`{summary.fps:.1f} FPS`) |

#### 👥 트래킹 멤버별 평균 일치율
{member_lines}
"""
        return summary.output_video_path, summary.report_image_path, md_report

    with gr.Blocks(title="Idol Choreography Sync Analyzer") as demo:
        gr.Markdown(
            """
            # 🕺 아이돌 칼군무(안무 일치율) AI 트래킹 & 동영상 합성기
            **YOLOv8-Pose + ByteTrack** 기반으로 멤버별 관절 각도와 팔다리 방향 벡터를 실시간 추적하여,
            **원본 영상의 원하는 구간 위에 스켈레톤(초록=일치, 빨강=오차), 멤버별 싱크로율 배지, 상단 게이지 및 실시간 파형 그래프**를 합성합니다.
            """
        )

        with gr.Row():
            with gr.Column(scale=1):
                video_input = gr.Video(label="1️⃣ 원본 안무 영상 업로드 (MP4 권장)")
                video_info_md = gr.Markdown("영상을 업로드하면 전체 길이가 자동으로 표시됩니다.")

                with gr.Group():
                    gr.Markdown("#### ⏱️ 2️⃣ 분석할 시간 구간(범위) 설정")
                    with gr.Row():
                        start_sec_slider = gr.Slider(
                            minimum=0.0,
                            maximum=300.0,
                            value=0.0,
                            step=0.5,
                            label="시작 시간 (초)",
                        )
                        end_sec_slider = gr.Slider(
                            minimum=0.0,
                            maximum=300.0,
                            value=0.0,
                            step=0.5,
                            label="종료 시간 (초, 0=끝까지)",
                        )

                with gr.Accordion("⚙️ 세부 모델 설정", open=False):
                    model_dropdown = gr.Dropdown(
                        choices=["yolov8n-pose.pt", "yolov8s-pose.pt", "yolov8m-pose.pt"],
                        value=default_model,
                        label="Pose 모델 크기 (n=빠름, s=균형, m=정밀)",
                    )
                    conf_slider = gr.Slider(
                        minimum=0.2,
                        maximum=0.7,
                        value=0.35,
                        step=0.05,
                        label="관절 감지 신뢰도 임계값 (Confidence)",
                    )

                analyze_btn = gr.Button(
                    "🚀 선택한 구간 칼군무 분석 & 오버레이 영상 합성 시작",
                    variant="primary",
                )

            with gr.Column(scale=1):
                video_output = gr.Video(label="합성된 칼군무 분석 영상 (선택 구간 HUD 오버레이 + 오디오)")
                report_img_output = gr.Image(label="선택 구간 싱크로율 & 멤버별 리포트 차트")
                report_md_output = gr.Markdown("영상을 업로드하고 **시작/종료 시간**을 지정한 뒤 분석 시작 버튼을 눌러주세요.")

        video_input.change(
            fn=on_video_upload,
            inputs=[video_input],
            outputs=[start_sec_slider, end_sec_slider, video_info_md],
        )

        analyze_btn.click(
            fn=process_video_ui,
            inputs=[video_input, start_sec_slider, end_sec_slider, model_dropdown, conf_slider],
            outputs=[video_output, report_img_output, report_md_output],
        )

    demo.launch(
        server_port=port,
        inbrowser=False,
        theme=gr.themes.Soft(primary_hue="emerald", neutral_hue="slate"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Idol Group Choreography Synchronization Analyzer")
    parser.add_argument("--input", "-i", type=str, help="Path to input dance video (.mp4)")
    parser.add_argument("--output", "-o", type=str, default="outputs", help="Output directory")
    parser.add_argument("--model", "-m", type=str, default="yolov8n-pose.pt", help="YOLOv8 pose model weight")
    parser.add_argument("--conf", type=float, default=0.35, help="Detection confidence threshold")
    parser.add_argument("--start", type=float, default=0.0, help="Start time in seconds")
    parser.add_argument("--end", type=float, default=None, help="End time in seconds")
    parser.add_argument("--max-frames", type=int, default=None, help="Max frames limit")
    parser.add_argument("--port", type=int, default=7860, help="Web UI port (default: 7860)")
    parser.add_argument("--ui", action="store_true", help="Launch Gradio Web UI")

    args = parser.parse_args()

    if args.ui or not args.input:
        print(f"🌐 Launching Idol Choreography Sync Analyzer Web UI on port {args.port}...")
        launch_gradio_ui(default_model=args.model, port=args.port)
    else:
        if not Path(args.input).exists():
            print(f"❌ Error: Input file not found: {args.input}")
            sys.exit(1)
        run_cli(
            input_video=args.input,
            output_dir=args.output,
            model_name=args.model,
            conf_thresh=args.conf,
            start_sec=args.start,
            end_sec=args.end,
            max_frames=args.max_frames,
        )


if __name__ == "__main__":
    main()
