"""
evaluate_metrics.py — 방어 성능 정량 평가 (혼동행렬·정밀도/재현율/F1·MTTD)
DAH 2026 Ghost Pilot 프로젝트

라벨이 있는 스트림(정상 + 점진적 위조, 공격 시작 시점 known)을 만들어
방어 파이프라인을 스텝별로 통과시키고 표준 지표를 산출한다.

  · 혼동행렬(TP/FP/TN/FN)
  · 정밀도(precision) / 재현율(recall) / F1
  · MTTD (Mean Time To Detect) — 공격 시작→첫 탐지까지 스텝(≈초 @1Hz)

두 방어를 동일 스트림으로 비교한다:
  OLD : Isolation Forest OR 윈도우 드리프트 (무상태)
  NEW : + 절대 이탈 추적 + 4상태 히스테리시스 (state==ATTACK을 탐지로 간주)

공정 비교를 위해 공격은 '고정 스텝'(비적응형)을 사용한다.
(적응형 백오프 회피 비교는 demo_stateful.py가 담당.)

산출물:
  ../results/metrics_summary.csv
  ../results/fig_confusion.png
  ../results/fig_metrics.png

사용법:
  python3 evaluate_metrics.py
"""

import csv
import os

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

import config
from defense_layer1 import RuleBasedDefense
from defense_layer2 import (
    AnomalyDetector, CumulativeDriftDetector, AbsoluteDriftTracker,
    KinematicConsistencyDetector)
from main_defense import DefenseStateMachine, state_action
from utils import FeatureExtractor, Log, ensure_src_cwd, FakeMsg

BASE_LAT, BASE_LON = 37.5665, 126.9780
ONSET = 15          # 공격 시작 스텝(0-based) — 그 전은 정상
LENGTH = 80
RATE_HZ = 1.0       # 텔레메트리 주기 가정 → 1스텝 ≈ 1초

# 한글 폰트 자동 탐지
_KFONTS = ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR"]
_HAS_KO = False
for _f in _KFONTS:
    if any(_f.lower() in f.name.lower() for f in fm.fontManager.ttflist):
        plt.rcParams["font.family"] = _f
        plt.rcParams["axes.unicode_minus"] = False
        _HAS_KO = True
        break


def L(ko, en):
    return ko if _HAS_KO else en


# FakeMsg는 utils.py 공용 정의 사용 (B4)


def build_iso():
    train = []
    with open("../data/normal_flight.csv") as f:
        reader = csv.reader(f)
        next(reader)
        for r in reader:
            train.append([float(x) for x in r])
    det = AnomalyDetector()
    det.model.fit(np.array(train))
    det.trained = True
    return det


def run_stream(step_m, seed, iso):
    """
    step_m=0 → 정상 스트림. step_m>0 → ONSET부터 점진적 위조.
    OLD/NEW 예측을 동일 위치 스트림에서 함께 계산해 반환.
    """
    rng = np.random.RandomState(seed)
    layer1 = RuleBasedDefense()
    drift = CumulativeDriftDetector()
    absd = AbsoluteDriftTracker()
    kin = KinematicConsistencyDetector()
    sm = DefenseStateMachine()
    ext = FeatureExtractor(fixed_dt=1.0)
    prev_state = "NORMAL"
    delta = 0.0
    rows = []

    for i in range(LENGTH):
        attacking = step_m > 0 and i >= ONSET
        if attacking:
            delta += step_m / 111000.0
        lat = BASE_LAT + rng.normal(0, 0.000003) + delta
        lon = BASE_LON + rng.normal(0, 0.000003) + delta
        msg = FakeMsg("GLOBAL_POSITION_INT",
                      lat=int(lat * 1e7), lon=int(lon * 1e7),
                      alt=20000, vx=0, vy=0, vz=0, seq=i, sys_id=1)

        l1_result, _ = layer1.check(msg)
        feat = ext.extract(msg)
        iso_flag = win = over = kin_bad = False
        if feat is not None:
            iso_flag, _ = iso.detect(feat)
            win, _ = drift.update(lat, lon)
            over, _ = absd.update(lat, lon)
            # 위조는 vx=vy=0 → 추측항법과 어긋남 (dt=1.0)
            kin_bad, _ = kin.update(lat, lon, 0.0, 0.0, 1.0)

        pred_old = int(iso_flag or win)

        anomaly = iso_flag or win or over or kin_bad
        state = sm.update(anomaly, over, l1_result == "BLOCK")
        if state == "NORMAL" and prev_state != "NORMAL":
            absd.set_anchor(lat, lon)
            kin.reset(lat, lon)
        prev_state = state
        pred_new = int(state == "ATTACK")

        rows.append({
            "step": i + 1,
            "label": int(attacking),
            "pred_old": pred_old,
            "pred_new": pred_new,
        })
    return rows


def confusion(rows, key):
    tp = fp = tn = fn = 0
    for r in rows:
        y, p = r["label"], r[key]
        if y == 1 and p == 1:
            tp += 1
        elif y == 0 and p == 1:
            fp += 1
        elif y == 0 and p == 0:
            tn += 1
        else:
            fn += 1
    return tp, fp, tn, fn


def prf(tp, fp, tn, fn):
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    acc = (tp + tn) / (tp + fp + tn + fn) if (tp + fp + tn + fn) else 0.0
    return prec, rec, f1, acc


def mttd_of_stream(rows, key):
    """공격 스트림에서 (공격시작→첫 탐지) 스텝. 미탐지면 None."""
    first_attack = ONSET + 1                    # 1-based 첫 공격 스텝
    for r in rows:
        if r["label"] == 1 and r[key] == 1:
            return r["step"] - first_attack
    return None


def maneuver_false_positive(seed=0, steps=60, speed_ms=2.0):
    """
    (C1 시연) 정상 기동 스트림(드론이 실제로 이동, 속도도 일치)에서
    '순수 위치 드리프트' 탐지와 '운동학 잔차' 탐지의 오탐을 비교한다.
      · 드리프트: 위치가 움직이므로 오탐(정상 기동을 위조로 착각).
      · 운동학  : 위치와 속도가 일치하므로 오탐하지 않음.
    반환: (drift_fp_rate%, kin_fp_rate%, 집계 스텝수)
    """
    rng = np.random.RandomState(seed)
    drift = CumulativeDriftDetector()
    kin = KinematicConsistencyDetector()
    step_deg = speed_ms / 111000.0     # 북쪽으로 speed_ms m/s (dt=1s)
    lat = BASE_LAT
    drift_fp = kin_fp = counted = 0
    for i in range(steps):
        lat += step_deg                       # 실제로 북쪽 이동
        lon = BASE_LON + rng.normal(0, 0.000003)
        d_det, _ = drift.update(lat, lon)
        k_det, _ = kin.update(lat, lon, speed_ms, 0.0, 1.0)  # vx=북속도 일치
        if i >= config.DRIFT_WINDOW:          # 윈도우가 찬 뒤부터 집계
            counted += 1
            drift_fp += int(d_det)
            kin_fp += int(k_det)
    dr = drift_fp / counted * 100 if counted else 0.0
    kr = kin_fp / counted * 100 if counted else 0.0
    return dr, kr, counted


def plot_maneuver(drift_fp, kin_fp, output="../results/fig_kinematic.png"):
    fig, ax = plt.subplots(figsize=(7, 5))
    bars = ax.bar(
        [L("순수 위치 드리프트", "Position drift"),
         L("운동학 잔차 (C1)", "Kinematic residual")],
        [drift_fp, kin_fp], color=["#C0392B", "#27AE60"], width=0.55)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
                f"{b.get_height():.0f}%", ha="center", fontsize=13,
                fontweight="bold")
    ax.set_ylabel(L("정상 기동 오탐율 (%)", "Maneuver false-positive (%)"),
                  fontsize=12)
    ax.set_ylim(0, 110)
    ax.set_title(
        L("정상 기동에서의 오탐 — 위치·속도 교차검증(C1)의 효과",
          "False positives on legit maneuver — kinematic cross-check"),
        fontsize=12)
    ax.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(output, dpi=150)
    plt.close(fig)
    Log.info(f"그래프 저장: {output}")


def main():
    ensure_src_cwd()
    Log.info("=" * 66)
    Log.info("방어 성능 정량 평가 — 혼동행렬·정밀도/재현율/F1·MTTD")
    Log.info("=" * 66)

    iso = build_iso()

    # ── 스트림 구성 ──
    normal_seeds = range(8)                      # 정상 8개
    attack_steps = [0.5, 1.0, 2.0, 5.0]          # 위조 스텝(m/스텝)
    attack_trials = 3                            # 스텝당 3회

    all_rows = []
    per_step = {s: {"old": [], "new": []} for s in attack_steps}  # MTTD 수집

    for sd in normal_seeds:
        all_rows.extend(run_stream(0, seed=1000 + sd, iso=iso))

    for s in attack_steps:
        for t in range(attack_trials):
            rows = run_stream(s, seed=int(s * 100) + t, iso=iso)
            all_rows.extend(rows)
            for key, tag in (("pred_old", "old"), ("pred_new", "new")):
                m = mttd_of_stream(rows, key)
                per_step[s][tag].append(m)

    # ── 전체 혼동행렬·지표 ──
    results = {}
    for key, name in (("pred_old", "OLD"), ("pred_new", "NEW")):
        tp, fp, tn, fn = confusion(all_rows, key)
        prec, rec, f1, acc = prf(tp, fp, tn, fn)
        results[name] = dict(tp=tp, fp=fp, tn=tn, fn=fn,
                             precision=prec, recall=rec, f1=f1, acc=acc)

    # ── MTTD 요약 (탐지된 것만 평균) ──
    def mttd_stats(tag):
        vals = [m for s in attack_steps for m in per_step[s][tag] if m is not None]
        miss = sum(1 for s in attack_steps for m in per_step[s][tag] if m is None)
        total = sum(len(per_step[s][tag]) for s in attack_steps)
        mean = sum(vals) / len(vals) if vals else float("nan")
        return mean, miss, total

    old_mttd, old_miss, total_atk = mttd_stats("old")
    new_mttd, new_miss, _ = mttd_stats("new")
    results["OLD"]["mttd"] = old_mttd
    results["NEW"]["mttd"] = new_mttd
    results["OLD"]["miss"] = old_miss
    results["NEW"]["miss"] = new_miss

    # 공정한 MTTD: '둘 다 탐지한' 스트림만으로 지연 비교(선택 편향 제거)
    mo, mn, n_matched = [], [], 0
    for s in attack_steps:
        for o, n in zip(per_step[s]["old"], per_step[s]["new"]):
            if o is not None and n is not None:
                mo.append(o); mn.append(n); n_matched += 1
    matched_old = sum(mo) / len(mo) if mo else float("nan")
    matched_new = sum(mn) / len(mn) if mn else float("nan")

    # ── 콘솔 출력 ──
    def show(name):
        r = results[name]
        Log.info(f"[{name}] 혼동행렬  TP={r['tp']} FP={r['fp']} "
                 f"TN={r['tn']} FN={r['fn']}")
        Log.info(f"       정밀도={r['precision']:.3f} 재현율={r['recall']:.3f} "
                 f"F1={r['f1']:.3f} 정확도={r['acc']:.3f}")
        mt = f"{r['mttd']:.1f}" if r['mttd'] == r['mttd'] else "N/A"
        Log.info(f"       MTTD={mt}스텝(≈초, 탐지분만) | "
                 f"미탐지 스트림 {r['miss']}/{total_atk}")

    Log.info("-" * 66)
    show("OLD")
    Log.info("-" * 66)
    show("NEW")
    Log.info("-" * 66)
    Log.info(f"공정 MTTD(둘 다 탐지한 {n_matched}개 스트림만): "
             f"OLD {matched_old:.1f} vs NEW {matched_new:.1f} 스텝 "
             f"→ 히스테리시스로 NEW가 {matched_new - matched_old:+.1f}스텝 지연(견고성 비용)")
    Log.info("-" * 66)

    # 스텝별 탐지·MTTD (느린 공격에서 격차가 크다)
    Log.info("스텝 크기별 탐지(탐지 스트림 수 / MTTD 스텝):")
    for s in attack_steps:
        od = [m for m in per_step[s]["old"] if m is not None]
        nd = [m for m in per_step[s]["new"] if m is not None]
        n = attack_trials
        om = f"{sum(od)/len(od):.1f}" if od else "  -"
        nm = f"{sum(nd)/len(nd):.1f}" if nd else "  -"
        Log.info(f"  {s:4.1f}m/스텝 → OLD {len(od)}/{n} (MTTD {om})  |  "
                 f"NEW {len(nd)}/{n} (MTTD {nm})")

    # ── (C1) 정상 기동 오탐 비교: 순수 위치 드리프트 vs 운동학 잔차 ──
    Log.info("-" * 66)
    Log.info("(C1) 정상 기동(위치 이동+속도 일치) 스트림 오탐율:")
    dr_list, kr_list = [], []
    for sd in range(5):
        dr, kr, _ = maneuver_false_positive(seed=sd)
        dr_list.append(dr)
        kr_list.append(kr)
    dr_mean = sum(dr_list) / len(dr_list)
    kr_mean = sum(kr_list) / len(kr_list)
    Log.info(f"  순수 위치 드리프트   → 오탐율 {dr_mean:5.1f}%  (정상 기동을 위조로 오판)")
    Log.info(f"  운동학 잔차 교차검증 → 오탐율 {kr_mean:5.1f}%  (위치·속도 일치로 통과)")
    Log.info("=" * 66)

    results["matched_old"] = matched_old
    results["matched_new"] = matched_new
    results["n_matched"] = n_matched

    # ── CSV 저장 ──
    out = "../results/metrics_summary.csv"
    os.makedirs("../results", exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["method", "TP", "FP", "TN", "FN",
                    "precision", "recall", "F1", "accuracy",
                    "MTTD_steps", "missed_streams", "total_attack_streams"])
        for name in ("OLD", "NEW"):
            r = results[name]
            w.writerow([name, r["tp"], r["fp"], r["tn"], r["fn"],
                        f"{r['precision']:.3f}", f"{r['recall']:.3f}",
                        f"{r['f1']:.3f}", f"{r['acc']:.3f}",
                        f"{r['mttd']:.1f}", r["miss"], total_atk])
    Log.info(f"지표 저장: {out}")

    plot_confusion(results)
    plot_metrics(results)
    plot_maneuver(dr_mean, kr_mean)
    Log.info("지표 그림 3장 생성 완료. 보고서 5번(성능 검증)에 삽입하세요.")


# ──────────────────────────────────────────────────────────────
def plot_confusion(results, output="../results/fig_confusion.png"):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    for ax, name in zip(axes, ("OLD", "NEW")):
        r = results[name]
        mat = np.array([[r["tp"], r["fn"]], [r["fp"], r["tn"]]])
        im = ax.imshow(mat, cmap="Blues")
        ax.set_xticks([0, 1]); ax.set_yticks([0, 1])
        ax.set_xticklabels([L("탐지", "Detect"), L("미탐지", "Miss")])
        ax.set_yticklabels([L("실제 공격", "Attack"), L("실제 정상", "Normal")])
        ax.set_title(L(f"{name} 혼동행렬", f"{name} confusion"), fontsize=12)
        thr = mat.max() / 2
        cells = [[(0, 0, "TP"), (0, 1, "FN")], [(1, 0, "FP"), (1, 1, "TN")]]
        for row in cells:
            for (ri, ci, tag) in row:
                v = mat[ri, ci]
                ax.text(ci, ri, f"{tag}\n{v}", ha="center", va="center",
                        color="white" if v > thr else "#1a1a1a",
                        fontsize=12, fontweight="bold")
    fig.suptitle(L("혼동행렬 비교 (동일 스트림)", "Confusion matrix (same streams)"),
                 fontsize=13)
    plt.tight_layout()
    plt.savefig(output, dpi=150)
    plt.close(fig)
    Log.info(f"그래프 저장: {output}")


def plot_metrics(results, output="../results/fig_metrics.png"):
    labels = [L("정밀도", "Precision"), L("재현율", "Recall"), "F1"]
    old = [results["OLD"]["precision"], results["OLD"]["recall"], results["OLD"]["f1"]]
    new = [results["NEW"]["precision"], results["NEW"]["recall"], results["NEW"]["f1"]]
    x = np.arange(len(labels)); w = 0.36

    fig, ax = plt.subplots(figsize=(8.5, 5))
    b1 = ax.bar(x - w / 2, old, w, color="#C0392B", label="OLD")
    b2 = ax.bar(x + w / 2, new, w, color="#27AE60", label="NEW")
    for bars in (b1, b2):
        for bar in bars:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                    f"{bar.get_height():.2f}", ha="center", fontsize=10)
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=12)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel(L("점수", "Score"), fontsize=12)
    om = results["OLD"]["miss"]; nm = results["NEW"]["miss"]
    mo = results["matched_old"]; mn = results["matched_new"]
    ax.set_title(
        L(f"방어 성능 비교  (미탐지 스트림 OLD {om} → NEW {nm} · "
          f"공정 MTTD {mo:.1f}→{mn:.1f}스텝)",
          f"Defense metrics  (missed streams OLD {om}→NEW {nm} · "
          f"matched MTTD {mo:.1f}→{mn:.1f})"),
        fontsize=12)
    ax.legend(fontsize=11)
    ax.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(output, dpi=150)
    plt.close(fig)
    Log.info(f"그래프 저장: {output}")


if __name__ == "__main__":
    main()
