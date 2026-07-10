"""
server.py — Ghost Pilot 폐루프 시뮬레이터 백엔드
FastAPI + WebSocket으로 프론트엔드에 실시간 데이터 전송

실행:
  pip install fastapi uvicorn
  python server.py

접속:
  브라우저에서 http://localhost:8000 열기
"""

import asyncio
import json
import math
import os
import random
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# ── src 경로 추가 (defense_layer1/2, config 등 import용) ──
SRC_DIR = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_DIR))

try:
    import numpy as np
    from defense_layer1 import RuleBasedDefense
    from defense_layer2 import (
        AnomalyDetector, CumulativeDriftDetector,
        AbsoluteDriftTracker, KinematicConsistencyDetector,
    )
    from main_defense import DefenseStateMachine, state_action
    from utils import FakeMsg, haversine_m
    OFFLINE_MODE = False
    print("[서버] 실제 방어 모듈 로드 완료")
except ImportError as e:
    print(f"[서버] 방어 모듈 로드 실패 ({e}) → 내장 시뮬레이션 모드로 전환")
    OFFLINE_MODE = True


# ══════════════════════════════════════════════════════════════
# 내장 방어 로직 (모듈 로드 실패 시 fallback)
# ══════════════════════════════════════════════════════════════
def _hav(la1, lo1, la2, lo2):
    R = 6371000
    r = math.pi / 180
    dp = (la2 - la1) * r
    dl = (lo2 - lo1) * r
    a = math.sin(dp/2)**2 + math.cos(la1*r)*math.cos(la2*r)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


class _CumDrift:
    def __init__(self, w=20, t=15):
        self.w = w; self.t = t; self.pos = []
    def update(self, la, lo):
        self.pos.append((la, lo))
        if len(self.pos) > self.w: self.pos.pop(0)
        if len(self.pos) < self.w: return False, 0.0
        d = _hav(self.pos[0][0], self.pos[0][1], la, lo)
        return d > self.t, d


class _AbsDrift:
    def __init__(self, t=25, wu=3):
        self.t = t; self.wu = wu; self.anc = None; self._s = 0
    def set_anchor(self, la, lo):
        self.anc = (la, lo); self._s = self.wu
    def update(self, la, lo):
        if self._s < self.wu:
            self._s += 1; self.anc = (la, lo); return False, 0.0
        d = _hav(self.anc[0], self.anc[1], la, lo)
        return d > self.t, d


class _KinDet:
    def __init__(self, t=15):
        self.t = t; self.ref = None; self.dN = 0; self.dE = 0
    def reset(self, la, lo):
        self.ref = (la, lo); self.dN = 0; self.dE = 0
    def update(self, la, lo, vx, vy, dt):
        if self.ref is None:
            self.reset(la, lo); return False, 0.0
        self.dN += vx * dt; self.dE += vy * dt
        sn = 1 if la >= self.ref[0] else -1
        se = 1 if lo >= self.ref[1] else -1
        aN = _hav(self.ref[0], self.ref[1], la, self.ref[1]) * sn
        aE = _hav(self.ref[0], self.ref[1], self.ref[0], lo) * se
        res = math.hypot(aN - self.dN, aE - self.dE)
        return res > self.t, res


class _SM:
    def __init__(self, cf=3, cl=5, rc=8):
        self.state = "NORMAL"; self.cf = cf; self.cl = cl; self.rc = rc
        self.aS = 0; self.cS = 0
    def update(self, an, abs_ov, blk):
        if an: self.aS += 1; self.cS = 0
        else: self.cS += 1; self.aS = 0
        s = self.state
        if blk: self.state = "ATTACK"
        elif s == "NORMAL":
            if an: self.state = "SUSPICIOUS"
        elif s == "SUSPICIOUS":
            if self.aS >= self.cf or abs_ov: self.state = "ATTACK"
            elif self.cS >= self.cl: self.state = "NORMAL"
        elif s == "ATTACK":
            if self.cS >= self.cl and not abs_ov: self.state = "RECOVERY"
        elif s == "RECOVERY":
            if an or abs_ov: self.state = "ATTACK"
            elif self.cS >= self.rc: self.state = "NORMAL"
        return self.state


# ══════════════════════════════════════════════════════════════
# 시뮬레이션 엔진
# ══════════════════════════════════════════════════════════════
BASE_LAT = 37.5665
BASE_LON = 126.9780
DECEIVE_M = 15.0


class SimEngine:
    """
    Python 실제 방어 로직으로 폐루프 시뮬레이션 한 스텝 실행.
    OFFLINE_MODE=False면 실제 defense_layer1/2 클래스 사용.
    """

    def __init__(self, config: dict):
        self.cfg = config
        self.step = 0
        self.spoof_delta = 0.0
        self.spoof_step = config.get("init_step_m", 1.0)
        self.detecting = False
        self.prev_state = "NORMAL"
        self.mttd = None
        self.backoff_count = 0
        self._rng = self._make_rng()

        dw = config.get("drift_window", 20)
        dt = config.get("drift_threshold", 15.0)
        cf = config.get("confirm", 3)

        if OFFLINE_MODE:
            self.cd = _CumDrift(dw, dt)
            self.ad = _AbsDrift(25, 3)
            self.kin = _KinDet(15)
            self.sm = _SM(cf, 5, 8)
        else:
            # 실제 방어 모듈
            self.cd = CumulativeDriftDetector(window=dw, drift_threshold_m=dt)
            self.ad = AbsoluteDriftTracker()
            self.kin = KinematicConsistencyDetector()
            self.sm = DefenseStateMachine(confirm=cf)

        self.def_mode = config.get("def_mode", "new")
        self.atk_mode = config.get("atk_mode", "adaptive")

        # Isolation Forest (실제 모듈 있을 때만)
        self.iso = None
        if not OFFLINE_MODE:
            try:
                MODEL_PATH = SRC_DIR.parent / "models" / "isoforest.pkl"
                self.iso = AnomalyDetector().load(str(MODEL_PATH))
            except Exception:
                pass

    def _make_rng(self):
        """재현 가능한 의사난수 (seed=42)"""
        state = {"s": 42}
        def rng():
            state["s"] = (state["s"] * 1664525 + 1013904223) & 0xFFFFFFFF
            return (state["s"] >> 1) / 0x7FFFFFFF
        def norm():
            u, v = 0, 0
            while not u: u = rng()
            while not v: v = rng()
            return math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * v)
        return norm

    def tick(self) -> dict:
        """한 스텝 실행 → 결과 dict 반환"""
        self.step += 1
        norm = self._rng
        cfg = self.cfg
        SMIN = 0.05
        SMAX = cfg.get("init_step_m", 1.0) * 4

        # ── 공격자 판단 ──
        atk_decide = "hold"
        am = self.atk_mode
        if am == "adaptive":
            if self.detecting:
                self.spoof_step = max(self.spoof_step * 0.5, SMIN)
                atk_decide = "backoff"
                self.backoff_count += 1
            else:
                self.spoof_step = min(self.spoof_step * 1.05, SMAX)
                atk_decide = "accel"
        elif am == "fixed-slow":
            self.spoof_step = 0.5
        elif am == "fixed-fast":
            self.spoof_step = 3.0
        elif am == "backoff":
            if self.step % 12 < 6:
                self.spoof_step = min(self.spoof_step * 1.15, SMAX)
                atk_decide = "accel"
            else:
                self.spoof_step = max(self.spoof_step * 0.25, SMIN)
                atk_decide = "backoff"
                self.backoff_count += 1

        self.spoof_delta += self.spoof_step

        # ── 위치 계산 ──
        perc_lat = BASE_LAT + self.spoof_delta / 111000 + norm() * 0.000003
        perc_lon = BASE_LON + self.spoof_delta / 111000 + norm() * 0.000003
        real_lat = BASE_LAT + norm() * 0.000003
        real_lon = BASE_LON + norm() * 0.000003

        # ── 방어 로직 ──
        if OFFLINE_MODE or self.def_mode == "new":
            # 누적 드리프트
            wD, wM = self.cd.update(perc_lat, perc_lon)
            # 절대 이탈
            aO, aM = self.ad.update(perc_lat, perc_lon)
            # 운동학
            kB, kM = self.kin.update(perc_lat, perc_lon, 0.0, 0.0, 1.0)
            # Isolation Forest
            iso_flag = False
            if self.iso:
                try:
                    from utils import FeatureExtractor
                    # 피처 근사 계산
                    feat = [
                        self.spoof_step / 111000,
                        self.spoof_step / 111000,
                        20.0, 0.0, 0.0, 0.0, 1,
                        self.spoof_step,
                    ]
                    iso_flag, _ = self.iso.detect(feat)
                except Exception:
                    pass

            anomaly = wD or aO or kB or iso_flag

            if self.def_mode == "old":
                # OLD: 윈도우 드리프트만, 무상태
                det = wD
                state = "ATTACK" if det else "NORMAL"
                action = "SWITCH" if det else "PASS"
                aM = aM  # 절대이탈은 계산만
            else:
                # NEW: 다중신호 + 4상태
                if OFFLINE_MODE:
                    state = self.sm.update(anomaly, aO, False)
                else:
                    state = self.sm.update(anomaly, aO, False)

                if state == "NORMAL" and self.prev_state != "NORMAL":
                    if OFFLINE_MODE:
                        self.ad.set_anchor(perc_lat, perc_lon)
                        self.kin.reset(perc_lat, perc_lon)
                    else:
                        self.ad.set_anchor(perc_lat, perc_lon)
                        self.kin.reset(perc_lat, perc_lon)

                det = state != "NORMAL"
                action_map = {
                    "NORMAL": "PASS", "SUSPICIOUS": "ALERT",
                    "ATTACK": "SWITCH", "RECOVERY": "HOVER",
                }
                action = action_map[state]
        else:
            wD, wM, aO, aM, kB, kM = False, 0.0, False, 0.0, False, 0.0
            det = False; state = "NORMAL"; action = "PASS"

        self.detecting = det
        self.prev_state = state

        if not self.mttd and det:
            self.mttd = self.step

        return {
            "step": self.step,
            "spoof_step": round(self.spoof_step, 3),
            "cum_m": round(self.spoof_delta, 2),
            "win_m": round(wM, 2),
            "abs_m": round(aM, 2),
            "win_det": bool(wD),
            "abs_over": bool(aO),
            "kin_bad": bool(kB),
            "state": state,
            "action": action,
            "detecting": bool(det),
            "atk_decide": atk_decide,
            "backoff_count": self.backoff_count,
            "mttd": self.mttd,
            "perc_lat": round(perc_lat, 7),
            "perc_lon": round(perc_lon, 7),
            "real_lat": round(real_lat, 7),
            "real_lon": round(real_lon, 7),
            "offline_mode": OFFLINE_MODE,
        }

    def reset(self, config: dict):
        self.__init__(config)


# ══════════════════════════════════════════════════════════════
# FastAPI 앱
# ══════════════════════════════════════════════════════════════
app = FastAPI(title="Ghost Pilot Simulator")

# 정적 파일 (index.html을 같은 폴더에 두면 자동 서빙)
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

# HTML 파일 읽기 (같은 디렉토리의 ghostpilot_ui.html)
UI_FILE = Path(__file__).parent / "ghostpilot_ui.html"


@app.get("/")
async def root():
    if UI_FILE.exists():
        return HTMLResponse(UI_FILE.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>ghostpilot_ui.html 파일을 server.py와 같은 폴더에 넣어주세요.</h2>")


@app.get("/status")
async def status():
    return {
        "offline_mode": OFFLINE_MODE,
        "src_dir": str(SRC_DIR),
        "modules_loaded": not OFFLINE_MODE,
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    print(f"[WS] 클라이언트 연결: {ws.client}")
    engine: SimEngine | None = None

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            cmd = msg.get("cmd")

            # ── 시작 ──
            if cmd == "start":
                cfg = msg.get("config", {})
                engine = SimEngine(cfg)
                await ws.send_json({
                    "type": "started",
                    "offline_mode": OFFLINE_MODE,
                    "total": cfg.get("length", 80),
                })
                print(f"[WS] 시뮬 시작: {cfg}")

            # ── 스텝 요청 ──
            elif cmd == "step":
                if engine is None:
                    await ws.send_json({"type": "error", "msg": "먼저 start 명령을 보내세요"})
                    continue
                data = engine.tick()
                await ws.send_json({"type": "step", "data": data})

            # ── 전체 실행 (자동) ──
            elif cmd == "run":
                if engine is None:
                    await ws.send_json({"type": "error", "msg": "먼저 start 명령을 보내세요"})
                    continue
                length = msg.get("length", 80)
                interval = msg.get("interval", 0.5)  # 초
                for _ in range(length):
                    data = engine.tick()
                    await ws.send_json({"type": "step", "data": data})
                    await asyncio.sleep(interval)
                    if data["step"] >= length:
                        break
                await ws.send_json({"type": "done", "mttd": engine.mttd})

            # ── 리셋 ──
            elif cmd == "reset":
                cfg = msg.get("config", {})
                if engine:
                    engine.reset(cfg)
                await ws.send_json({"type": "reset_ok"})

            # ── 핑 ──
            elif cmd == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        print(f"[WS] 클라이언트 연결 해제: {ws.client}")
    except Exception as e:
        print(f"[WS] 오류: {e}")
        try:
            await ws.send_json({"type": "error", "msg": str(e)})
        except Exception:
            pass


if __name__ == "__main__":
    print("=" * 55)
    print(" Ghost Pilot 폐루프 시뮬레이터 서버")
    print(f" 모드: {'내장 시뮬레이션' if OFFLINE_MODE else '실제 방어 모듈'}")
    print(f" URL : http://localhost:8000")
    print("=" * 55)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
