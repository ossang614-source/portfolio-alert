"""
Portfolio System v4.0 — 200일선 기반 2단계 동적 자산배분
======================================================
설계 근거: 메인계좌 26년 백테스트(2000-08 ~ 2026-08, BRK.B+비상스위치+SCHP20/GLD20 최종구성)
  CAGR 10.1% / MDD -17.2% / 최악의 해 -10.2%(2022) — 최초 VOO단순버전(8.6%/-17.6%)에서 개선
  (비교: VOO 100% = CAGR 6.9% / MDD -52.5%)

원칙: "많이 따는 것보다 다 잃지 않는 것"
  - 낙폭 상한을 10년·15년·20년·26년 전 구간에서 일정하게 유지
  - 폭락 없는 기간엔 VOO 대비 연 3~5%p 뒤짐 (보험료)

v3.0 대비 제거된 것: ET/V0.5(C)/V1.0 3단계, V0.25(BRK), CAPE, SCHD·SHV·VOO 자산,
  카카오톡 발송, phase_state 경로의존 규칙 → 백테스트에서 성과 기여 없거나 마이너스로 삭제.
  단, RSI·다이버전스는 2026-08 "비상 선제 공격 스위치" 목적으로 제한적으로 재도입
  (방어 상태에서 200일선 회복을 기다리지 않고 조기 공격 전환 — get_market_data 참고).

2026-08 SCHP/금 비중 조정(SCHP25→20, 금15→20 / 방어 SCHP55→50, 금15→20):
  미국 국채(SCHP) 신뢰도 우려(재정불신 리스크) 반영. 2022년 SCHP -9.4%로 방어자산이
  오히려 손실을 낸 사례(금리급등형 위기, 채권-주식 동반하락) 대응. 금리모멘텀 감지
  오버레이(동적 조정)도 검토했으나 복잡도 대비 실익이 낮아 기각, 정적 배분 조정만 채택.
  백테스트: CAGR 10.0%→10.1%, MDD -17.1%→-17.2%(거의 동일), 2022년 -10.6%→-10.2%.
"""
import yfinance as yf
import requests
import smtplib
import json
import numpy as np
import pandas as pd
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

EMAIL_FROM = "ossang614@gmail.com"
EMAIL_TO   = "ossang614@gmail.com"
EMAIL_PASS = "fuvw zbun ydje supp"

# ============================================================
# 단계별 목표 비중 — 계좌별 2개 세트
#   메인(일반계좌): 코스피 비과세 활용
#   연금저축·IRP: 코스피 제외(연금 인출세 부과로 비과세 실익 없음) +
#                 IRP 규정(위험자산 70% 상한, 안전자산 30% 이상) 준수
#   전환 신호(200일선)는 세 계좌 모두 동일하게 적용
# ============================================================
TARGETS = {
    "공격": {"133690.KS": 25, "BRK-B": 25, "102110.KS": 10, "0072R0.KS": 20, "SCHP": 20},
    "방어": {"133690.KS": 10, "BRK-B": 10, "102110.KS": 10, "0072R0.KS": 20, "SCHP": 50},
}

# 연금저축·IRP는 계좌 제약이 서로 달라 배분을 분리:
#   연금저축: 담보대출 계좌라 펀드만 가능. 장기채는 재정불신 리스크 및 회복지연기
#             취약성(24년 백테스트로 확인)이 커서 배제 — 초단기채 단일 구성.
#   IRP: 담보대출 없어 ETF 가능. 실제 물가연동채(TIPS) ETF를 직접 매수 가능해 SCHP 단일 구성.
#   둘 다 코스피 제외(연금소득세 부과로 비과세 실익 없음), 위험자산 70% 상한(IRP 법정 기준) 준수.
PENSION_TARGETS = {
    # 연금저축은 담보대출 계좌라 펀드만 가능 — RISE 버크셔TOP10은 ETF라 편입 불가.
    # 순수 버크셔 추종 "펀드"(증권자투자신탁) 상품은 확인되지 않아 S&P500 펀드 유지.
    # 2026-08 금/안전자산 비중 조정(금15→20, 안전자산 -5%p): 미국채 신뢰도 우려 반영,
    # 메인계좌와 동일 논리. 백테스트: CAGR 8.3%→8.7%, MDD 동일, 2022년 -12.7%→-12.5%.
    "연금저축": {
        "공격": {"QQQ_PEN": 30, "SP500_PEN": 25, "GOLD_PEN": 20, "STC_PEN": 25},
        "방어": {"QQQ_PEN": 10, "SP500_PEN": 10, "GOLD_PEN": 20, "STC_PEN": 60},
    },
    # IRP는 담보대출이 없어 ETF 매매 가능 — RISE 버크셔포트폴리오TOP10(국내상장 ETF) 사용.
    # 2026-08 금/안전자산 비중 조정: 백테스트 CAGR 10.5%→10.7%, MDD 동일, 2022년 -10.0%→-9.5%.
    "IRP": {
        "공격": {"QQQ_PEN": 30, "BRK_PEN": 25, "GOLD_PEN": 20, "SCHP_PEN": 25},
        "방어": {"QQQ_PEN": 10, "BRK_PEN": 10, "GOLD_PEN": 20, "SCHP_PEN": 60},
    },
}
PENSION_SLOT_NAMES = {
    "QQQ_PEN":   "나스닥100 상품",
    "SP500_PEN": "S&P500 펀드 (연금저축용, ETF 편입불가)",
    "BRK_PEN":   "RISE 버크셔포트폴리오TOP10(475350, ETF, IRP전용) — BRK.B 27.5%+13F상위10종목",
    "GOLD_PEN":  "금 상품",
    "SCHP_PEN":  "물가연동채(TIPS) ETF",
    "STC_PEN":   "초단기채 펀드",
}
# 연금저축·IRP 보유 현황 — 상품명은 실제 계좌 상품으로 매칭해 직접 채울 것
PENSION_HOLDINGS = {
    "연금저축": {
        "QQQ_PEN":   {"value": 0,          "name": "신한미국나스닥100인덱스(UH)C-pe — 신규매수 필요"},
        "SP500_PEN": {"value": 9_113_568,  "name": "삼성미국S&P500인덱스증권자투자신탁UH_C — 보유중, 비중확대 필요"},
        "GOLD_PEN":  {"value": 19_949_212, "name": "KB스타골드특별자산투자신탁C-Pe"},
        "STC_PEN":   {"value": 5_871_105,  "name": "NH-Amundi USD초단기채권 (비중확대 필요, 삼성/KB단기채는 매도예정)"},
        "매도대상_기타": {"value": 12_292_321+11_044_217+16_922_063,
                       "name": "삼성달러표시단기채권+KB글로벌단기채(→NH-Amundi로 통합)+NH-Amundi필승코리아(코스피, 매도)"},
        "현금": {"value": 2_319_351, "name": "현금"},
    },
    "IRP": {
        "QQQ_PEN":   {"value": 0,          "name": "TIGER 미국나스닥100(133690) — 신규매수 필요"},
        "BRK_PEN":   {"value": 0,          "name": "RISE 버크셔포트폴리오TOP10(475350) — 신규매수 필요"},
        "GOLD_PEN":  {"value": 0,          "name": "TIGER KRX금현물(0072R0) — 신규매수 필요"},
        "SCHP_PEN":  {"value": 0,          "name": "KODEX iShares 미국인플레이션국채액티브(468370) — 신규매수 필요"},
        "매도대상_기타": {"value": 11_672_540+27_264_525,
                       "name": "ACE 나스닥100미국채혼합 + TIGER 미국S&P500(→RISE버크셔로 교체) 전부 매도"},
        "현금": {"value": 73_188, "name": "현금"},
    },
}

# 세액공제 목적 연간 신규 납입 계획 (매년 초 납입 가정)
PENSION_ANNUAL_CONTRIB = {
    "연금저축": 6_000_000,
    "IRP": 3_000_000,
}

# 슬롯 표시명 및 자산군 분류 (이메일 표시용)
SLOT_NAMES = {
    "133690.KS": "TIGER 미국나스닥100",
    "360750.KS": "TIGER 미국S&P500",  # 메인계좌 목표에서 제외됨(BRK-B로 교체), 연금계좌 참고용 유지
    "BRK-B":     "버크셔 해서웨이 B주 (해외주식계좌)",
    "102110.KS": "TIGER 200 (코스피)",
    "0072R0.KS": "금 그룹 (TIGER KRX금현물+GLD)",
    "SCHP":      "물가연동채 그룹",
}
# 자산군: (분류명, 색상) — 성격이 같은 자산끼리 묶어 위험 구조를 한눈에 보이게 함
SLOT_CLASS = {
    "133690.KS": ("주식 · 미국 성장주", "#818cf8"),
    "360750.KS": ("주식 · 미국 대형주", "#22c55e"),
    "BRK-B":     ("주식 · 미국 대형주(개별주)", "#3b82f6"),
    "102110.KS": ("주식 · 국내",        "#a78bfa"),
    "0072R0.KS": ("실물자산 · 금",      "#eab308"),
    "SCHP":      ("안전자산 · 채권",    "#f472b6"),
}
# 위험/안전 구분 (요약 표시용)
RISK_SLOTS = ("133690.KS", "360750.KS", "102110.KS", "BRK-B")
SAFE_SLOTS = ("0072R0.KS", "SCHP")

# 전환 규칙
DEFENSE_TRIGGER = 0.97   # 공격→방어: S&P500 < 200일선 × 0.97
MIN_HOLD_DAYS   = 63     # 방어→공격 시 최소 보유 거래일(약 3개월)

STATE_FILE = "phase_state.json"

# 보유 수량 (매매 시 직접 갱신)
HOLDINGS = {
    "133690.KS": {"qty": 0,   "type": "kr", "name": "TIGER 미국나스닥100"},
    "360750.KS": {"qty": 0,   "type": "kr", "name": "TIGER 미국S&P500"},
    "102110.KS": {"qty": 69,  "type": "kr", "name": "TIGER 200"},
    "0072R0.KS": {"qty": 0,   "type": "kr", "name": "TIGER KRX금현물"},
    # GLD(미국상장) — 2026-08 매도 완료. 매도대금은 TIGER KRX금현물 등 v4.0 재배분에 사용.
    "GLD":       {"qty": 0,   "type": "us", "name": "GLD (해외주식계좌, 금 그룹 일부 — 비과세공제 활용, 매수량 미정)"},
    "SCHP":      {"qty": 0,   "type": "us", "name": "SCHP"},
    "468370.KS": {"qty": 917, "type": "kr", "name": "KODEX 미국인플레이션국채액티브"},
    "329750.KS": {"qty": 68,  "type": "kr", "name": "TIGER 미국달러단기채권액티브"},
    # 정리 대상 — 2026-08 전량 매도 완료: 4.9% 대출 2,872만원 상환 +
    # 잔여는 ISA 국내상장 ETF(TIGER 미국나스닥100 등)로 재편입.
    "BRK-B":     {"qty": 0,  "type": "us", "name": "BRK.B (2026-08 재편입 결정 — 신규매수 필요, 해외주식계좌)"},
    "SCHD":      {"qty": 0, "type": "us", "name": "SCHD (매도완료)"},
    "VOO":       {"qty": 0,  "type": "us", "name": "VOO (매도완료)"},
}
# SCHP 슬롯은 국내 대체 ETF와 합산 관리
SCHP_GROUP = ("SCHP", "468370.KS", "329750.KS")
# 금 슬롯 그룹 — TIGER KRX금현물(국내) + GLD(해외, 연 250만원 비과세 공제 활용 목적)
# 고정 비율 없이 보유한 만큼 합산해 목표 20%에 반영 (BRK.B와 같은 취지)
GOLD_GROUP = ("0072R0.KS", "GLD")
BTC_ADDRESS = "bc1q57h8sn3ykge2yh2kn46dq5gsqn92x7pl6uanlg"


def get_market_data():
    """
    S&P500 종가·200일선·RSI(14)·비상스위치 신호 조회.
    비상스위치(2026-08 신설, 26년 백테스트 근거): 방어 상태에서 200일선 회복을 기다리지
    않고 즉시 공격 전환하는 조건. 다음 중 하나 충족 시 발동:
      ① 200일선 -20% 이탈 AND 주봉 RSI 강세 다이버전스 (닷컴형 장기침체 포착: -15%→-20% 조정이 근소하게 우수, CAGR+0.1%p)
      ② 일봉 RSI(14) ≤ 20 (코로나형 급락 포착: RSI≤15 최적화 결과 20이 더 우수, 2020-02 등 13건 포착)
    백테스트: 미적용 CAGR 9.3%/MDD-17.3% → 적용 CAGR 9.9%~9.8%/MDD-17.1%(방어력 손실 없이 수익 개선).
    한계: 26년간 표본이 적어(다이버전스 4건·RSI 13건) 통계적 견고성은 제한적.
    반환: (종가, 200일선, 이격도%, RSI, 비상신호bool, 비상사유, 오류)
    """
    try:
        hist = yf.Ticker("^GSPC").history(period="2y")
        if hist is None or hist.empty:
            return None, None, None, None, False, None, "S&P500 조회 실패(응답 없음)"
        hist = hist.dropna(subset=["Close"])
        if len(hist) < 200:
            return None, None, None, None, False, None, f"S&P500 데이터 {len(hist)}행 — 200일 미만"

        close_s = hist["Close"]
        close = float(close_s.iloc[-1])
        ma200 = float(close_s.rolling(200).mean().iloc[-1])
        dev = (close / ma200 - 1) * 100

        c = close_s.values
        delta = c[1:] - c[:-1]
        gain = pd.Series(np.where(delta > 0, delta, 0)).rolling(14).mean().values
        loss = pd.Series(np.where(delta < 0, -delta, 0)).rolling(14).mean().values
        rs = gain / loss
        rsi_series = 100 - 100 / (1 + rs)
        rsi = float(rsi_series[-1]) if len(rsi_series) and rsi_series[-1] == rsi_series[-1] else None

        emergency, emg_reason = False, None
        # 조건②: 일봉 RSI≤20
        if rsi is not None and rsi <= 20:
            emergency, emg_reason = True, f"일봉 RSI({rsi:.1f})≤20 — 코로나형 급락 포착 신호"

        # 조건①: 200일선 -20% 이탈 + 주봉 RSI 강세 다이버전스
        if not emergency and dev <= -20:
            wk = close_s.resample("W").last().dropna()
            if len(wk) >= 20:
                wc = wk.values
                delta_w = wc[1:] - wc[:-1]
                gw = pd.Series(np.where(delta_w > 0, delta_w, 0)).rolling(14).mean().values
                lw = pd.Series(np.where(delta_w < 0, -delta_w, 0)).rolling(14).mean().values
                rsi_w = 100 - 100 / (1 + gw / lw)
                i = len(wc) - 2  # delta 배열은 wc보다 1개 짧으므로 마지막 주는 rsi_w[-1]에 대응
                if wc[-1] <= wc[max(0, len(wc)-8):].min():
                    seg = wc[max(0, len(wc)-17):len(wc)-4]
                    rseg = rsi_w[max(0, len(rsi_w)-16):len(rsi_w)-3] if len(rsi_w) >= 17 else np.array([])
                    if len(seg) > 0 and len(rseg) == len(seg):
                        p = seg.argmin()
                        if rseg[p] == rseg[p] and wc[-1] < seg[p] and rsi_w[-1] > rseg[p]:
                            emergency, emg_reason = True, f"200일선 {dev:+.1f}% 이탈 + 주봉 RSI 강세 다이버전스 — 닷컴형 장기침체 바닥 포착 신호"

        return round(close, 2), round(ma200, 2), round(dev, 2), (round(rsi, 1) if rsi is not None else None), emergency, emg_reason, None
    except Exception as e:
        return None, None, None, None, False, None, f"S&P500 조회 예외: {type(e).__name__}: {e}"


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
            return s.get("phase", "공격"), s.get("since", None), s.get("since_attack", None)
    except (FileNotFoundError, json.JSONDecodeError):
        return "공격", None, None


def save_state(phase, since, since_attack=None):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"phase": phase, "since": since, "since_attack": since_attack,
                       "updated": datetime.now().strftime("%Y-%m-%d %H:%M")},
                      f, ensure_ascii=False)
    except Exception as e:
        print(f"[경고] 상태 저장 실패: {e}")


def decide_phase(close, ma200, prev_phase, since, since_attack=None, emergency=False, emg_reason=None):
    """
    2단계 전환 판정 (2026-08 개정: 최소유예 + 비상 선제 공격 스위치).
      공격 → 방어: 종가 < 200일선 × 0.97 AND 공격 진입 후 10거래일(약 14일) 경과
        — 2011-11-08(공격 복귀)→11-09(방어 재전환) 같은 하루짜리 헛발동 방지 목적.
          26년 백테스트: 헛발동 제거, 성과 동일(CAGR 9.2→9.3%, MDD -17.3% 그대로).
          버퍼 확대(-4~7%)는 MDD가 오히려 악화되어 기각, 최소유예만 채택.
      방어 → 공격(정상): 종가 > 200일선 AND 방어 진입 후 63거래일(약 90일) 경과
      방어 → 공격(비상): 200일선 회복·최소보유 무시하고 emergency=True 즉시 전환
        — get_market_data()의 비상신호(RSI≤20 또는 200일선-20%+주봉다이버전스) 근거.
          26년 백테스트: 미적용 CAGR 9.3%/MDD-17.3% → 적용 CAGR 9.9%/MDD-17.1%(방어력 손실 없이 개선).
          표본 적음(다이버전스4건·RSI13건)에 유의.
    반환: (단계, 전환여부, 사유)
    """
    today = datetime.now().date()
    if prev_phase == "공격":
        if close < ma200 * DEFENSE_TRIGGER:
            att_days = None
            if since_attack:
                try:
                    att_days = (today - datetime.strptime(since_attack, "%Y-%m-%d").date()).days
                except (ValueError, TypeError):
                    att_days = None
            if att_days is None or att_days >= 14:
                return "방어", True, f"S&P500이 200일선 -3%({ma200*DEFENSE_TRIGGER:,.0f}) 아래로 이탈 + 최소유예 충족({att_days}일) — 방어 전환"
            return "공격", False, f"200일선 -3% 이탈했으나 최소유예 미충족({att_days}/14일) — 헛발동 방지"
        return "공격", False, None
    # 방어 상태
    if emergency:
        return "공격", True, f"🚨 비상 선제 공격 전환: {emg_reason}"
    if close > ma200:
        days = None
        if since:
            try:
                days = (today - datetime.strptime(since, "%Y-%m-%d").date()).days
            except (ValueError, TypeError):
                days = None
        if days is None or days >= 90:
            return "공격", True, f"S&P500이 200일선 회복 + 최소보유 충족({days}일) — 공격 전환"
        return "방어", False, f"200일선은 회복했으나 최소보유 미충족({days}/90일)"
    return "방어", False, None


def get_prices(tickers):
    """티커별 현재가와 환율 조회."""
    prices = {}
    for t in tickers:
        try:
            h = yf.Ticker(t).history(period="5d")
            h = h.dropna(subset=["Close"]) if h is not None and not h.empty else None
            prices[t] = float(h["Close"].iloc[-1]) if h is not None and len(h) else None
        except Exception:
            prices[t] = None
    try:
        h = yf.Ticker("USDKRW=X").history(period="5d").dropna(subset=["Close"])
        usdkrw = float(h["Close"].iloc[-1]) if len(h) else None
    except Exception:
        usdkrw = None
    return prices, usdkrw


def get_portfolio(phase, usdkrw, prices):
    """
    현재 평가액과 목표 대비 이탈을 계산.
    환율 조회 실패 시 달러 자산을 원화로 오계산하면 비중이 크게 왜곡되므로,
    이 경우 계산을 포기하고 (None, 0, 0, 경고) 반환한다.
    """
    if usdkrw is None:
        return None, 0, 0, "환율(USDKRW) 조회 실패 — 비중 계산 생략"
    targets = TARGETS[phase]
    values = {}
    missing = []
    for tk, info in HOLDINGS.items():
        p = prices.get(tk)
        if info["qty"] == 0:
            values[tk] = 0.0
            continue
        if p is None:
            values[tk] = 0.0
            missing.append(info["name"])
            continue
        v = p * info["qty"]
        values[tk] = v * usdkrw if info["type"] == "us" else v
    # SCHP·금 슬롯은 그룹 합산
    slot_values = {}
    for slot in targets:
        if slot == "SCHP":
            slot_values[slot] = sum(values.get(t, 0.0) for t in SCHP_GROUP)
        elif slot == "0072R0.KS":
            slot_values[slot] = sum(values.get(t, 0.0) for t in GOLD_GROUP)
        else:
            slot_values[slot] = values.get(slot, 0.0)
    excluded = sum(values.get(t, 0.0) for t in ("SCHD", "VOO", "360750.KS"))
    total = sum(slot_values.values()) + excluded
    if total <= 0:
        return None, 0, excluded, "보유 자산 평가액 0 — 가격 조회 실패 추정"
    rows = []
    for slot, tgt in targets.items():
        cur_pct = slot_values[slot] / total * 100
        band = min(5.0, tgt * 0.25)   # 5/25룰
        rows.append({
            "slot": slot, "target": tgt, "cur": round(cur_pct, 1),
            "diff": round(cur_pct - tgt, 1), "band": round(band, 1),
            "over": abs(cur_pct - tgt) > band,
            "value": round(slot_values[slot]),
        })
    warn = f"시세 조회 실패: {', '.join(missing)}" if missing else None
    return rows, round(total), round(excluded), warn


def get_btc():
    try:
        r = requests.get(f"https://blockchain.info/balance?active={BTC_ADDRESS}", timeout=10)
        bal = r.json()[BTC_ADDRESS]["final_balance"] / 1e8
        h = yf.Ticker("BTC-USD").history(period="5d").dropna(subset=["Close"])
        usd = float(h["Close"].iloc[-1]) if len(h) else None
        return bal, usd
    except Exception:
        return None, None


def get_pension_status(account_name, phase):
    """
    연금저축·IRP 계좌의 목표 대비 현황 계산.
    두 계좌는 상품 제약이 달라(연금저축=펀드전용·장기채배제, IRP=ETF가능·TIPS단일)
    PENSION_TARGETS[account_name]로 서로 다른 목표 배분을 사용.
    실제 시세 조회 없이 PENSION_HOLDINGS에 기록된 평가액(수동 갱신)을 사용 —
    연금계좌 상품은 야후 파이낸스로 조회 불가한 국내 펀드가 대부분이므로,
    보유현황 캡처를 볼 때마다 이 값을 직접 갱신하는 방식으로 운용.
    """
    targets = PENSION_TARGETS.get(account_name, {}).get(phase, {})
    hold = PENSION_HOLDINGS.get(account_name, {})
    core_total = sum(v["value"] for k, v in hold.items() if k in targets)
    other = sum(v["value"] for k, v in hold.items() if k not in targets)
    total = core_total + other
    if total <= 0:
        return None, 0, 0
    rows = []
    for slot, tgt in targets.items():
        cur_val = hold.get(slot, {}).get("value", 0)
        cur_pct = cur_val / total * 100
        band = min(5.0, tgt * 0.25)
        rows.append({
            "slot": slot, "name": hold.get(slot, {}).get("name", "-"),
            "target": tgt, "cur": round(cur_pct, 1),
            "diff": round(cur_pct - tgt, 1), "band": round(band, 1),
            "over": abs(cur_pct - tgt) > band,
        })
    return rows, round(total), round(other)


def build_html(now, close, ma200, dev, phase, changed, reason, rows, total, excluded,
               btc_bal, btc_usd, usdkrw, err, pension_data=None):
    color = "#16a34a" if phase == "공격" else "#0284c7"
    bg_light = "#f0fdf4" if phase == "공격" else "#eff6ff"
    tgt = TARGETS[phase]
    risk_t = sum(v for k, v in tgt.items() if k in RISK_SLOTS)
    safe_t = sum(v for k, v in tgt.items() if k in SAFE_SLOTS)
    risk_c = sum(r["cur"] for r in (rows or []) if r["slot"] in RISK_SLOTS)
    safe_c = sum(r["cur"] for r in (rows or []) if r["slot"] in SAFE_SLOTS)
    rebal = "".join(
        f"""<tr style="background:{'#ffffff' if i % 2 == 0 else '#f8fafc'};border-bottom:1px solid #e2e8f0">
          <td style="padding:10px 14px;color:#1e293b;font-weight:600">{SLOT_NAMES.get(r['slot'], r['slot'])}
            <div style="color:{SLOT_CLASS.get(r['slot'],('','#64748b'))[1]};font-size:10px;margin-top:2px;font-weight:400">{SLOT_CLASS.get(r['slot'],('',''))[0]}</div>
          </td>
          <td style="padding:10px 14px;color:#64748b;text-align:right">{r['target']}%</td>
          <td style="padding:10px 14px;color:#0f172a;text-align:right;font-family:monospace;font-weight:700">{r['cur']}%</td>
          <td style="padding:10px 14px;text-align:right;font-family:monospace;font-weight:700;color:{'#dc2626' if r['over'] else '#94a3b8'}">{r['diff']:+.1f}%p</td>
          <td style="padding:10px 14px;color:#94a3b8;text-align:right">±{r['band']}%p</td>
        </tr>""" for i, r in enumerate(rows or []))
    return f"""<html><body style="background:#f1f5f9;color:#1e293b;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;margin:0">
  <div style="max-width:680px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1)">
  <div style="padding:28px">
    <div style="font-size:11px;color:#94a3b8;letter-spacing:0.15em;font-weight:700">PORTFOLIO SYSTEM v4.0</div>
    <div style="font-size:20px;color:#0f172a;margin:6px 0 20px;font-weight:700">{now}</div>

    {f'<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:14px;margin-bottom:20px;color:#b91c1c;font-weight:600">⚠️ {err}</div>' if err else ''}

    <!-- 메인계좌 헤더 -->
    <div style="background:{bg_light};border:1.5px solid {color};border-radius:10px;padding:20px;margin-bottom:24px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <span style="background:{color};color:#fff;font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px">메인계좌</span>
        <span style="font-size:11px;color:#64748b;letter-spacing:0.1em">현재 단계</span>
      </div>
      <div style="font-size:28px;font-weight:800;color:{color};margin:4px 0">{phase}</div>
      {f'<div style="color:#b45309;font-size:13px;margin-top:8px;font-weight:600">🔀 {reason}</div>' if changed and reason else ''}
      {f'<div style="color:#64748b;font-size:12px;margin-top:8px">{reason}</div>' if (not changed and reason) else ''}
    </div>

    <div style="font-size:11px;color:#94a3b8;letter-spacing:0.1em;margin-bottom:10px;font-weight:700">▸ 전환 지표 (200일선)</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px;background:#f8fafc;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">
      <tr style="border-bottom:1px solid #e2e8f0"><td style="padding:10px 14px;color:#64748b">S&amp;P500 종가</td><td style="padding:10px 14px;text-align:right;color:#0f172a;font-family:monospace;font-weight:700">{close:,.2f}</td></tr>
      <tr style="border-bottom:1px solid #e2e8f0"><td style="padding:10px 14px;color:#64748b">200일선</td><td style="padding:10px 14px;text-align:right;color:#0f172a;font-family:monospace;font-weight:700">{ma200:,.2f}</td></tr>
      <tr style="border-bottom:1px solid #e2e8f0"><td style="padding:10px 14px;color:#64748b">이격도</td><td style="padding:10px 14px;text-align:right;font-family:monospace;font-weight:700;color:{'#16a34a' if dev>0 else '#dc2626'}">{dev:+.2f}%</td></tr>
      <tr><td style="padding:10px 14px;color:#64748b">방어 전환선 (-3%)</td><td style="padding:10px 14px;text-align:right;color:#64748b;font-family:monospace">{ma200*DEFENSE_TRIGGER:,.2f}</td></tr>
    </table>

    <div style="font-size:11px;color:#94a3b8;letter-spacing:0.1em;margin-bottom:10px;font-weight:700">▸ 자산군 구성</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:20px;background:#f8fafc;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">
      <tr style="border-bottom:1px solid #e2e8f0">
        <td style="padding:12px 14px;color:#64748b">🔴 위험자산 <span style="color:#94a3b8;font-size:11px">(주식)</span></td>
        <td style="padding:12px 14px;text-align:right;font-family:monospace;font-weight:700;color:#0f172a">{risk_c:.1f}% <span style="color:#94a3b8;font-weight:400">/ 목표 {risk_t}%</span></td>
      </tr>
      <tr>
        <td style="padding:12px 14px;color:#64748b">🔵 안전자산 <span style="color:#94a3b8;font-size:11px">(채권·금)</span></td>
        <td style="padding:12px 14px;text-align:right;font-family:monospace;font-weight:700;color:#0f172a">{safe_c:.1f}% <span style="color:#94a3b8;font-weight:400">/ 목표 {safe_t}%</span></td>
      </tr>
    </table>

    <div style="font-size:11px;color:#94a3b8;letter-spacing:0.1em;margin-bottom:10px;font-weight:700">▸ 목표 비중 대비 현황 ({phase})</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:8px;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">
      <tr style="background:#1e293b">
        <td style="padding:8px 14px;color:#cbd5e1;font-size:11px;font-weight:700">자산</td>
        <td style="padding:8px 14px;color:#cbd5e1;font-size:11px;text-align:right;font-weight:700">목표</td>
        <td style="padding:8px 14px;color:#cbd5e1;font-size:11px;text-align:right;font-weight:700">현재</td>
        <td style="padding:8px 14px;color:#cbd5e1;font-size:11px;text-align:right;font-weight:700">이탈</td>
        <td style="padding:8px 14px;color:#cbd5e1;font-size:11px;text-align:right;font-weight:700">허용밴드</td>
      </tr>
      {rebal}
    </table>
    <div style="color:#64748b;font-size:11px;margin-bottom:28px">총 평가액 {total:,}원{f' · 미편입 자산(SCHD·VOO·GLD·TIGER 미국S&P500) {excluded:,}원 포함 — v4.0 배분 외, 매도 후 재배분 필요' if excluded else ''}</div>

    {"".join(_pension_block(name, phase, d) for name, d in (pension_data or {}).items())}

    {f'''<div style="font-size:11px;color:#94a3b8;letter-spacing:0.1em;margin-bottom:10px;font-weight:700">▸ ₿ BTC (별도 관리)</div>
    <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:14px;margin-bottom:24px;color:#92400e;font-size:13px;font-weight:600">
      {btc_bal} BTC{f' · ${btc_usd:,.0f}' if btc_usd else ''}
    </div>''' if btc_bal else ''}

    <div style="color:#94a3b8;font-size:11px;border-top:1px solid #e2e8f0;padding-top:16px;margin-top:8px;line-height:1.7">
      Portfolio System v4.0 · 26년 백테스트 CAGR 8.6% / MDD -17.6%<br>
      전환: 공격→방어(200일선 -3% 이탈) / 방어→공격(200일선 회복 + 90일 경과)<br>
      연금저축·IRP는 코스피 제외(연금소득세 부과로 비과세 실익 없음) + 위험자산 70% 상한 준수
    </div>
  </div>
  </div>
</body></html>"""


_ACCOUNT_COLORS = {"연금저축": "#9333ea", "IRP": "#ea580c"}


def _pension_block(account_name, phase, data):
    """연금저축·IRP 계좌 블록 HTML 생성 (계좌별 고유 색상으로 구분)"""
    rows, total, other = data
    if not rows:
        return ""
    acc_color = _ACCOUNT_COLORS.get(account_name, "#64748b")
    body = "".join(
        f"""<tr style="background:{'#ffffff' if i % 2 == 0 else '#f8fafc'};border-bottom:1px solid #e2e8f0">
          <td style="padding:10px 14px;color:#1e293b;font-weight:600">{PENSION_SLOT_NAMES.get(r['slot'], r['slot'])}
            <div style="color:#94a3b8;font-size:10px;margin-top:2px;font-weight:400">{r['name']}</div>
          </td>
          <td style="padding:10px 14px;color:#64748b;text-align:right">{r['target']}%</td>
          <td style="padding:10px 14px;color:#0f172a;text-align:right;font-family:monospace;font-weight:700">{r['cur']}%</td>
          <td style="padding:10px 14px;text-align:right;font-family:monospace;font-weight:700;color:{'#dc2626' if r['over'] else '#94a3b8'}">{r['diff']:+.1f}%p</td>
        </tr>""" for i, r in enumerate(rows))
    return f"""
    <div style="display:flex;align-items:center;gap:8px;margin:28px 0 10px">
      <span style="background:{acc_color};color:#fff;font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px">{account_name}</span>
      <span style="font-size:11px;color:#94a3b8;letter-spacing:0.1em;font-weight:700">▸ {phase}</span>
    </div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:8px;border-radius:8px;overflow:hidden;border:1.5px solid {acc_color}33">
      <tr style="background:{acc_color}"><td colspan="4" style="padding:2px"></td></tr>
      {body}
    </table>
    <div style="color:#64748b;font-size:11px;margin-bottom:8px">총 평가액 {total:,}원{f' · 계좌 규정상 제외 대상 {other:,}원 포함' if other else ''}</div>
"""


def send_email(subject, html):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO
    msg.attach(MIMEText(html, 'html'))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login(EMAIL_FROM, EMAIL_PASS)
        s.send_message(msg)


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{now}] 지표 수집 시작...")

    close, ma200, dev, rsi, emergency, emg_reason, err = get_market_data()
    if err:
        print(f"[오류] {err}")
        send_email(f"[Portfolio] 데이터 오류 — {now}",
                   f"<html><body style='background:#070707;color:#f87171;padding:24px'>⚠️ {err}</body></html>")
        return

    prev_phase, since, since_attack = load_state()
    phase, changed, reason = decide_phase(close, ma200, prev_phase, since, since_attack, emergency, emg_reason)
    if changed:
        today_str = datetime.now().strftime("%Y-%m-%d")
        if phase == "방어":
            since = today_str          # 방어 진입일 갱신(다음 90일 카운트 시작)
        elif phase == "공격":
            since_attack = today_str   # 공격 진입일 갱신(다음 14일 카운트 시작)
        print(f"[전환] {prev_phase} → {phase}: {reason}")
    save_state(phase, since, since_attack)

    prices, usdkrw = get_prices(list(HOLDINGS.keys()))
    rows, total, excluded, port_warn = get_portfolio(phase, usdkrw, prices)
    btc_bal, btc_usd = get_btc()

    # 연금저축·IRP는 메인과 동일한 200일선 신호(phase)로 판단, 배분만 별도
    pension_data = {name: get_pension_status(name, phase) for name in PENSION_HOLDINGS}

    subject = f"[Portfolio v4.0] {'🔀 ' + prev_phase + '→' + phase if changed else phase} — {now}"
    html = build_html(now, close, ma200, dev, phase, changed, reason,
                      rows, total, excluded, btc_bal, btc_usd, usdkrw, port_warn,
                      pension_data)
    send_email(subject, html)
    print(f"✅ 이메일 발송 완료 (단계: {phase}, 이격도 {dev:+.2f}%)")


if __name__ == "__main__":
    main()
