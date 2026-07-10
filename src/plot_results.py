"""
plot_results.py — 실험 결과 시각화
DAH 2026 Ghost Pilot 프로젝트

run_experiments.py가 생성한 결과를 그래프로 그린다.
보고서에 삽입할 그림을 생성한다.

사용법:
  python3 plot_results.py
"""

import csv
import matplotlib
matplotlib.use("Agg")   # GUI 없는 환경 대응
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

from utils import Log, ensure_src_cwd

# 한글 폰트 자동 탐지 (있으면 사용, 없으면 영어 라벨로 대체)
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


def load_results(path="../results/experiment_results.csv"):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def plot_gradual_comparison(rows, output="../results/fig_detection_comparison.png"):
    """
    점진적 위조에 대한 Isolation Forest vs 누적 드리프트 탐지율 비교.
    이 그래프가 '왜 두 방식이 필요한가'를 시각적으로 증명한다.
    """
    iso_steps, iso_rates = [], []
    drift_steps, drift_rates = [], []

    for r in rows:
        if r["실험"] == "실험2_점진적위조":
            step = float(r["조건"].replace("m", ""))
            iso_steps.append(step)
            iso_rates.append(float(r["탐지율(%)"]))
        elif r["실험"] == "실험2B_점진적위조":
            step = float(r["조건"].replace("m지속", ""))
            drift_steps.append(step)
            drift_rates.append(float(r["탐지율(%)"]))

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(iso_steps, iso_rates, "o-", color="#C0392B",
            label=L("Isolation Forest (단일 스텝)", "Isolation Forest (single step)"),
            linewidth=2, markersize=8)
    ax.plot(drift_steps, drift_rates, "s-", color="#27AE60",
            label=L("누적 드리프트 (Cumulative Drift)", "Cumulative Drift"),
            linewidth=2, markersize=8)

    ax.set_xlabel(L("위조 스텝 크기 (m)", "Spoof step size (m)"), fontsize=12)
    ax.set_ylabel(L("탐지율 (%)", "Detection rate (%)"), fontsize=12)
    ax.set_title(L("점진적 위조 탐지: 단일 스텝 vs 누적 드리프트",
                   "Gradual Spoofing: Single-step vs Cumulative Drift"),
                 fontsize=13)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.set_ylim(-5, 105)

    ax.annotate(L("1m 위조:\nIsoForest 실패\nDrift 성공",
                  "1m spoof:\nIsoForest fails\nDrift succeeds"),
                xy=(1, 50), xytext=(2.5, 55),
                fontsize=9, color="#555",
                arrowprops=dict(arrowstyle="->", color="#888"))

    plt.tight_layout()
    plt.savefig(output, dpi=150)
    Log.info(f"그래프 저장: {output}")


def plot_summary_bar(rows, output="../results/fig_summary.png"):
    """전체 실험 요약 막대그래프"""
    labels = [
        L("급격한 위조\n(150m)", "Abrupt\n(150m)"),
        L("점진적 위조\n(1m, IsoForest)", "Gradual\n(1m, IsoForest)"),
        L("점진적 위조\n(1m, Drift)", "Gradual\n(1m, Drift)"),
        L("정상 데이터\n(오탐율)", "Normal\n(False Pos.)"),
    ]
    values = []

    # 값 추출
    d = {(r["실험"], r["조건"]): float(r["탐지율(%)"]) for r in rows}
    values.append(d.get(("실험1_급격한위조", "150m"), 0))
    values.append(d.get(("실험2_점진적위조", "1.0m"), 0))
    values.append(d.get(("실험2B_점진적위조", "1.0m지속"), 0))
    values.append(d.get(("실험3_오탐율", "정상데이터"), 0))

    colors = ["#27AE60", "#C0392B", "#27AE60", "#F39C12"]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(labels, values, color=colors)
    ax.set_ylabel(L("비율 (%)", "Rate (%)"), fontsize=12)
    ax.set_title(L("Ghost Pilot 방어 성능 요약",
                   "Ghost Pilot Defense Performance Summary"), fontsize=13)
    ax.set_ylim(0, 110)

    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 2,
                f"{val:.0f}%", ha="center", fontsize=11, fontweight="bold")

    plt.tight_layout()
    plt.savefig(output, dpi=150)
    Log.info(f"그래프 저장: {output}")


if __name__ == "__main__":
    ensure_src_cwd()
    rows = load_results()
    plot_gradual_comparison(rows)
    plot_summary_bar(rows)
    Log.info("모든 그래프 생성 완료. 보고서에 삽입하세요.")
