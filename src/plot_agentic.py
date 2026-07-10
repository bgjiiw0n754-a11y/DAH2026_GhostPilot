"""
plot_agentic.py — 에이전트 폐루프 시각화 (Ghost Pilot)
DAH 2026 Ghost Pilot 프로젝트

demo_adaptive.py가 생성한 ../results/adaptive_loop.csv를 읽어
보고서용 핵심 그림 3장을 만든다.

  fig_divergence_map.png  : 실제 경로 vs GCS 인식 경로 (2D) — 기만의 핵심 그림
  fig_divergence_time.png : 실제↔인식 이탈량의 시간 변화 + 탐지 시점
  fig_adaptive_saw.png    : 적응형 공격의 위조 속도 톱니(백오프↔가속)

한글 폰트가 있으면 한글, 없으면 영어 라벨로 자동 대체한다.

사용법:
  python3 demo_adaptive.py     # 먼저 데이터 생성
  python3 plot_agentic.py
"""

import csv
import os

import matplotlib
matplotlib.use("Agg")   # GUI 없는 환경 대응
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

from utils import Log, haversine_m, ensure_src_cwd

CSV_PATH = "../results/adaptive_loop.csv"
STATEFUL_CSV = "../results/stateful_loop.csv"
DECEIVE_M = 15.0

# 한글 폰트 자동 탐지
_KOREAN_FONTS = ["Malgun Gothic", "AppleGothic", "NanumGothic",
                 "Noto Sans CJK KR", "UnDotum"]
_HAS_KOREAN = False
for _f in _KOREAN_FONTS:
    if any(_f.lower() in f.name.lower() for f in fm.fontManager.ttflist):
        plt.rcParams["font.family"] = _f
        plt.rcParams["axes.unicode_minus"] = False
        _HAS_KOREAN = True
        break


def L(ko, en):
    """한글 폰트가 있으면 한글, 없으면 영어 라벨 반환"""
    return ko if _HAS_KOREAN else en


def load_rows(path=CSV_PATH):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def detection_spans(steps, flags):
    """detecting==1인 연속 구간을 [(start_step, end_step), ...]로 반환"""
    spans, start = [], None
    for s, fl in zip(steps, flags):
        if fl and start is None:
            start = s
        elif not fl and start is not None:
            spans.append((start, s))
            start = None
    if start is not None:
        spans.append((start, steps[-1] + 1))
    return spans


def first_detect(steps, flags):
    for s, fl in zip(steps, flags):
        if fl:
            return s
    return None


# ──────────────────────────────────────────────────────────────
# Fig 1: 실제 경로 vs GCS 인식 경로 (2D 지도)
# ──────────────────────────────────────────────────────────────
def plot_divergence_map(rows, output="../results/fig_divergence_map.png"):
    base_lat = float(rows[0]["real_lat"])
    base_lon = float(rows[0]["real_lon"])

    def to_xy(lat, lon):
        # 기준점 대비 동/북 방향 미터 오프셋
        x = haversine_m(base_lat, base_lon, base_lat, lon) * (1 if lon >= base_lon else -1)
        y = haversine_m(base_lat, base_lon, lat, base_lon) * (1 if lat >= base_lat else -1)
        return x, y

    real_xy = [to_xy(float(r["real_lat"]), float(r["real_lon"])) for r in rows]
    perc_xy = [to_xy(float(r["perc_lat"]), float(r["perc_lon"])) for r in rows]
    rx, ry = zip(*real_xy)
    px, py = zip(*perc_xy)

    steps = [int(r["step"]) for r in rows]
    flags = [int(r["detecting"]) for r in rows]
    fd = first_detect(steps, flags)

    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    # 실제 상태: 드론은 제자리 정지비행 (군집)
    ax.scatter(rx, ry, s=18, color="#27AE60", alpha=0.7,
               label=L("실제 상태 (드론 정지비행)", "Real state (drone loiter)"),
               zorder=3)
    # GCS 인식 상태: 위조로 서서히 이탈
    ax.plot(px, py, "-", color="#C0392B", linewidth=1.6,
            label=L("GCS 인식 상태 (위조 주입)", "GCS-perceived (spoofed)"),
            zorder=2)
    ax.scatter(px, py, s=10, color="#C0392B", alpha=0.5, zorder=2)

    ax.scatter([0], [0], marker="*", s=280, color="#2C3E50",
               edgecolor="white", linewidth=1, zorder=5,
               label=L("출발점 (실제=인식)", "Start (real=perceived)"))

    if fd is not None:
        dx, dy = perc_xy[fd - 1]
        ax.scatter([dx], [dy], marker="X", s=180, color="#000000", zorder=6,
                   label=L(f"방어 탐지 ({fd}스텝)", f"Detected (step {fd})"))
        ax.annotate(
            L("이 지점에서 방어가\n누적 이탈을 탐지",
              "Defense detects\ncumulative drift here"),
            xy=(dx, dy), xytext=(dx * 0.35, dy + 8), fontsize=9, color="#333",
            arrowprops=dict(arrowstyle="->", color="#888"))

    ax.set_xlabel(L("동쪽 방향 (m)", "East offset (m)"), fontsize=12)
    ax.set_ylabel(L("북쪽 방향 (m)", "North offset (m)"), fontsize=12)
    ax.set_title(L("실제 경로 vs GCS 인식 경로 — 상태 분리(기만)",
                   "Real vs GCS-perceived path — state separation"),
                 fontsize=13)
    ax.legend(fontsize=10, loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.set_aspect("equal", adjustable="datalim")
    plt.tight_layout()
    plt.savefig(output, dpi=150)
    plt.close(fig)
    Log.info(f"그래프 저장: {output}")


# ──────────────────────────────────────────────────────────────
# Fig 2: 실제↔인식 이탈량의 시간 변화 + 탐지 시점
# ──────────────────────────────────────────────────────────────
def plot_divergence_time(rows, output="../results/fig_divergence_time.png"):
    steps = [int(r["step"]) for r in rows]
    div = [float(r["divergence_m"]) for r in rows]
    net = [float(r["window_net_m"]) for r in rows]
    flags = [int(r["detecting"]) for r in rows]
    fd = first_detect(steps, flags)

    fig, ax = plt.subplots(figsize=(9, 5))
    # 탐지 구간 음영
    for a, b in detection_spans(steps, flags):
        ax.axvspan(a - 0.5, b - 0.5, color="#C0392B", alpha=0.08)

    ax.plot(steps, div, "-", color="#C0392B", linewidth=2,
            label=L("실제↔GCS 인식 이탈량", "Real↔perceived divergence"))
    ax.plot(steps, net, "--", color="#2980B9", linewidth=1.5,
            label=L("방어 관측 드리프트(윈도우 순변위)",
                    "Defense drift (window net)"))
    ax.axhline(15.0, color="#7F8C8D", linewidth=1, linestyle=":",
               label=L("드리프트 임계값 15m", "Drift threshold 15m"))

    if fd is not None:
        ax.axvline(fd, color="#27AE60", linewidth=1.5)
        ax.annotate(L(f"첫 탐지(MTTD)\n{fd}스텝", f"First detect\nstep {fd}"),
                    xy=(fd, 15), xytext=(fd + 2, 30), fontsize=9, color="#1E7A45",
                    arrowprops=dict(arrowstyle="->", color="#27AE60"))

    ax.set_xlabel(L("스텝", "Step"), fontsize=12)
    ax.set_ylabel(L("거리 (m)", "Distance (m)"), fontsize=12)
    ax.set_title(L("실제 상태 vs GCS 인식 상태 이탈량 (탐지 시점 표시)",
                   "State divergence over time (detection marked)"),
                 fontsize=13)
    ax.legend(fontsize=10, loc="upper left")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output, dpi=150)
    plt.close(fig)
    Log.info(f"그래프 저장: {output}")


# ──────────────────────────────────────────────────────────────
# Fig 3: 적응형 공격의 위조 속도 톱니 (백오프 ↔ 가속)
# ──────────────────────────────────────────────────────────────
def plot_adaptive_saw(rows, output="../results/fig_adaptive_saw.png"):
    steps = [int(r["step"]) for r in rows]
    step_m = [float(r["spoof_step_m"]) for r in rows]
    flags = [int(r["detecting"]) for r in rows]

    fig, ax = plt.subplots(figsize=(9, 5))
    for a, b in detection_spans(steps, flags):
        ax.axvspan(a - 0.5, b - 0.5, color="#C0392B", alpha=0.10,
                   label="_nolegend_")

    ax.plot(steps, step_m, "-o", color="#8E44AD", markersize=4, linewidth=1.6,
            label=L("위조 스텝 크기(공격 속도)", "Spoof step size (attack speed)"))

    # 탐지 구간 음영 설명용 프록시
    ax.plot([], [], color="#C0392B", alpha=0.3, linewidth=8,
            label=L("방어 탐지 구간 → 백오프", "Detected span → back-off"))

    ax.set_xlabel(L("스텝", "Step"), fontsize=12)
    ax.set_ylabel(L("스텝당 위조량 (m)", "Spoof per step (m)"), fontsize=12)
    ax.set_title(L("적응형 공격 — 탐지되면 백오프, 안 걸리면 가속 (살아있는 판단)",
                   "Adaptive attack — back off when detected, ramp up otherwise"),
                 fontsize=13)
    ax.legend(fontsize=10, loc="upper right")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output, dpi=150)
    plt.close(fig)
    Log.info(f"그래프 저장: {output}")


# ──────────────────────────────────────────────────────────────
# Fig 4: 4상태 방어(NEW) vs 단일 윈도우 방어(OLD) before/after
# ──────────────────────────────────────────────────────────────
def plot_stateful(path=STATEFUL_CSV, output="../results/fig_stateful.png"):
    rows = load_rows(path)
    old = [r for r in rows if r["mode"] == "OLD"]
    new = [r for r in rows if r["mode"] == "NEW"]
    steps = [int(r["step"]) for r in old]
    old_cum = [float(r["cum_drift_m"]) for r in old]
    new_cum = [float(r["cum_drift_m"]) for r in new]

    def cell_colors(series):
        cols = []
        for r in series:
            cum = float(r["cum_drift_m"])
            det = int(r["detecting"])
            if cum <= DECEIVE_M:
                cols.append("#BDC3C7")          # 아직 실질 기만 아님(회색)
            elif det:
                cols.append("#27AE60")          # 기만 중 탐지(초록)
            else:
                cols.append("#C0392B")          # 기만 중 미탐지=회피(빨강)
        return cols

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10, 7), gridspec_kw={"height_ratios": [2, 1]})

    # 상단: 누적 기만량 억제 비교
    ax1.plot(steps, old_cum, "--", color="#C0392B", linewidth=2,
             label=L("OLD: 윈도우+무상태", "OLD: window + stateless"))
    ax1.plot(steps, new_cum, "-", color="#27AE60", linewidth=2.2,
             label=L("NEW: 다중신호+4상태", "NEW: multi-signal + 4-state"))
    ax1.axhline(DECEIVE_M, color="#7F8C8D", linestyle=":", linewidth=1,
                label=L("실질 기만 기준 15m", "deception ref 15m"))
    ax1.set_ylabel(L("누적 기만량 (m)", "Cumulative deception (m)"), fontsize=12)
    ax1.set_title(L("4상태 히스테리시스 방어 — 백오프 회피 차단 효과",
                    "4-state hysteresis defense — blocks back-off evasion"),
                  fontsize=13)
    ax1.legend(fontsize=10, loc="upper left")
    ax1.grid(True, alpha=0.3)

    # 하단: 스텝별 커버리지 (초록=탐지, 빨강=회피, 회색=기만이전)
    ax2.scatter(steps, [1] * len(steps), c=cell_colors(old),
                marker="s", s=55)
    ax2.scatter(steps, [0] * len(steps), c=cell_colors(new),
                marker="s", s=55)
    ax2.set_yticks([0, 1])
    ax2.set_yticklabels([L("NEW", "NEW"), L("OLD", "OLD")])
    ax2.set_ylim(-0.6, 1.6)
    ax2.set_xlabel(L("스텝", "Step"), fontsize=12)
    ax2.set_title(L("스텝별 커버리지  (초록=탐지, 빨강=기만중 미탐지=회피, 회색=기만이전)",
                    "Per-step coverage (green=detected, red=evaded, gray=pre-deception)"),
                  fontsize=11)
    ax2.grid(True, axis="x", alpha=0.3)

    plt.tight_layout()
    plt.savefig(output, dpi=150)
    plt.close(fig)
    Log.info(f"그래프 저장: {output}")


if __name__ == "__main__":
    ensure_src_cwd()
    rows = load_rows()
    Log.info(f"데이터 로딩: {len(rows)}행")
    plot_divergence_map(rows)
    plot_divergence_time(rows)
    plot_adaptive_saw(rows)
    if os.path.exists(STATEFUL_CSV):
        plot_stateful()
    else:
        Log.alert(f"{STATEFUL_CSV} 없음 → demo_stateful.py 먼저 실행하면 "
                  f"fig_stateful.png도 생성됨")
    Log.info("에이전트 폐루프 그림 생성 완료. 보고서 5·6번에 삽입하세요.")
    if not _HAS_KOREAN:
        Log.alert("한글 폰트 미탐지 → 영어 라벨로 생성됨 "
                  "(한글 라벨 원하면 Malgun Gothic/NanumGothic 설치)")
