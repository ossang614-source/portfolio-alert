"""
Portfolio System v4.0 — 200일선 기반 2단계 동적 자산배분
======================================================
설계 근거: S&P500 26년 백테스트(2000-08 ~ 2026-08)
  CAGR 8.6% / MDD -17.6% / 손실연도 4회 / 최악의 해 -14.0%
  (비교: VOO 100% = CAGR 6.9% / MDD -52.5%)

원칙: "많이 따는 것보다 다 잃지 않는 것"
  - 낙폭 상한 -17.6%가 10년·15년·20년·26년 전 구간에서 일정하게 유지됨
  - 폭락 없는 기간엔 VOO 대비 연 3~5%p 뒤짐 (보험료)

v3.0 대비 제거된 것: ET/V0.5(C)/V1.0 3단계, V0.25(BRK), RSI·VIX·CAPE·다이버전스 지표,
  BRK.B·SCHD·SHV 자산, 카카오톡 발송, phase_state 경로의존 규칙
  → 전부 백테스트에서 성과 기여가 없거나 마이너스로 확인되어 삭제
"""
import yfinance as yf
import requests
import smtplib
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

EMAIL_FROM = "ossang614@gmail.com"
EMAIL_TO   = "ossang614@gmail.com"
EMAIL_PASS = "fuvw zbun ydje supp"

# ============================================================
# 단계별 목표 비중 (2단계)
# ============================================================
TARGETS = {
    "공격": {"QQQ": 25, "360750.KS": 20, "102110.KS": 15, "GLD": 15, "SCHP": 25},
    "방어": {"QQQ": 10, "360750.KS": 10, "102110.KS": 10, "GLD": 15, "SCHP": 55},
}

# 슬롯 표시명 (이메일 표시용)
SLOT_NAMES = {
    "QQQ":       "QQQ (나스닥100)",
    "360750.KS": "TIGER 미국S&P500",
    "102110.KS": "TIGER 200 (코스피)",
    "GLD":       "GLD (금)",
    "SCHP":      "물가연동채 그룹",
}

# 전환 규칙
DEFENSE_TRIGGER = 0.97   # 공격→방어: S&P500 < 200일선 × 0.97
MIN_HOLD_DAYS   = 63     # 방어→공격 시 최소 보유 거래일(약 3개월)

STATE_FILE = "phase_state.json"

# 보유 수량 (매매 시 직접 갱신)
HOLDINGS = {
    "QQQ":       {"qty": 0,   "type": "us", "name": "QQQ"},
    "360750.KS": {"qty": 0,   "type": "kr", "name": "TIGER 미국S&P500"},
    "102110.KS": {"qty": 69,  "type": "kr", "name": "TIGER 200"},
    "GLD":       {"qty": 19,  "type": "us", "name": "GLD"},
    "SCHP":      {"qty": 0,   "type": "us", "name": "SCHP"},
    "468370.KS": {"qty": 917, "type": "kr", "name": "KODEX 미국인플레이션국채액티브"},
    "329750.KS": {"qty": 68,  "type": "kr", "name": "TIGER 미국달러단기채권액티브"},
    # 정리 대상(v4.0 배분에서 제외됨) — 매도 후 0으로 변경
    "BRK-B":     {"qty": 24,  "type": "us", "name": "BRK.B (정리대상)"},
    "SCHD":      {"qty": 139, "type": "us", "name": "SCHD (정리대상)"},
    "VOO":       {"qty": 15,  "type": "us", "name": "VOO (→360750.KS로 통합)"},
}
# SCHP 슬롯은 국내 대체 ETF와 합산 관리
SCHP_GROUP = ("SCHP", "468370.KS", "329750.KS")
BTC_ADDRESS = "bc1q57h8sn3ykge2yh2kn46dq5gsqn92x7pl6uanlg"


def get_market_data():
    """S&P500 종가와 200일선 조회. 반환: (종가, 200일선, 이격도%, 오류)"""
    try:
        hist = yf.Ticker("^GSPC").history(period="1y")
        if hist is None or hist.empty:
            return None, None, None, "S&P500 조회 실패(응답 없음)"
        hist = hist.dropna(subset=["Close"])
        if len(hist) < 200:
            return None, None, None, f"S&P500 데이터 {len(hist)}행 — 200일 미만"
        close = float(hist["Close"].iloc[-1])
        ma200 = float(hist["Close"].rolling(200).mean().iloc[-1])
        dev = (close / ma200 - 1) * 100
        return round(close, 2), round(ma200, 2), round(dev, 2), None
    except Exception as e:
        return None, None, None, f"S&P500 조회 예외: {type(e).__name__}: {e}"


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
            return s.get("phase", "공격"), s.get("since", None)
    except (FileNotFoundError, json.JSONDecodeError):
        return "공격", None


def save_state(phase, since):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"phase": phase, "since": since,
                       "updated": datetime.now().strftime("%Y-%m-%d %H:%M")},
                      f, ensure_ascii=False)
    except Exception as e:
        print(f"[경고] 상태 저장 실패: {e}")


def decide_phase(close, ma200, prev_phase, since):
    """
    2단계 전환 판정.
      공격 → 방어: 종가 < 200일선 × 0.97 (즉시)
      방어 → 공격: 종가 > 200일선 AND 방어 진입 후 63거래일(약 90일) 경과
    반환: (단계, 전환여부, 사유)
    """
    today = datetime.now().date()
    if prev_phase == "공격":
        if close < ma200 * DEFENSE_TRIGGER:
            return "방어", True, f"S&P500이 200일선 -3%({ma200*DEFENSE_TRIGGER:,.0f}) 아래로 이탈 — 방어 전환"
        return "공격", False, None
    # 방어 상태
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
    """현재 평가액과 목표 대비 이탈을 계산."""
    targets = TARGETS[phase]
    values = {}
    for tk, info in HOLDINGS.items():
        p = prices.get(tk)
        if p is None or info["qty"] == 0:
            values[tk] = 0.0
            continue
        v = p * info["qty"]
        values[tk] = v * usdkrw if info["type"] == "us" and usdkrw else v
    # SCHP 슬롯은 그룹 합산
    slot_values = {}
    for slot in targets:
        if slot == "SCHP":
            slot_values[slot] = sum(values.get(t, 0.0) for t in SCHP_GROUP)
        else:
            slot_values[slot] = values.get(slot, 0.0)
    excluded = sum(values.get(t, 0.0) for t in ("BRK-B", "SCHD", "VOO"))
    total = sum(slot_values.values()) + excluded
    if total <= 0:
        return None, 0, excluded
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
    return rows, round(total), round(excluded)


def get_btc():
    try:
        r = requests.get(f"https://blockchain.info/balance?active={BTC_ADDRESS}", timeout=10)
        bal = r.json()[BTC_ADDRESS]["final_balance"] / 1e8
        h = yf.Ticker("BTC-USD").history(period="5d").dropna(subset=["Close"])
        usd = float(h["Close"].iloc[-1]) if len(h) else None
        return bal, usd
    except Exception:
        return None, None


def build_html(now, close, ma200, dev, phase, changed, reason, rows, total, excluded,
               btc_bal, btc_usd, usdkrw, err):
    color = "#22c55e" if phase == "공격" else "#38bdf8"
    rebal = "".join(
        f"""<tr style="border-bottom:1px solid #1a1a1a">
          <td style="padding:8px 14px;color:#ddd">{SLOT_NAMES.get(r['slot'], r['slot'])}</td>
          <td style="padding:8px 14px;color:#888;text-align:right">{r['target']}%</td>
          <td style="padding:8px 14px;color:#fff;text-align:right;font-family:monospace">{r['cur']}%</td>
          <td style="padding:8px 14px;text-align:right;font-family:monospace;color:{'#ef4444' if r['over'] else '#666'}">{r['diff']:+.1f}%p</td>
          <td style="padding:8px 14px;color:#666;text-align:right">±{r['band']}%p</td>
        </tr>""" for r in (rows or []))
    return f"""<html><body style="background:#070707;color:#ddd;font-family:-apple-system,sans-serif;padding:24px">
  <div style="max-width:680px;margin:0 auto">
    <div style="font-size:11px;color:#555;letter-spacing:0.15em">PORTFOLIO SYSTEM v4.0</div>
    <div style="font-size:20px;color:#fff;margin:6px 0 20px">{now}</div>

    {f'<div style="background:#1a0000;border:1px solid #442222;border-radius:6px;padding:12px;margin-bottom:20px;color:#f87171">⚠️ {err}</div>' if err else ''}

    <div style="background:{'#001400' if phase=='공격' else '#00121a'};border:1px solid {color};border-radius:8px;padding:18px;margin-bottom:24px">
      <div style="font-size:11px;color:#666;letter-spacing:0.1em">현재 단계</div>
      <div style="font-size:28px;font-weight:700;color:{color};margin:4px 0">{phase}</div>
      {f'<div style="color:#facc15;font-size:13px;margin-top:8px">🔀 {reason}</div>' if changed and reason else ''}
      {f'<div style="color:#888;font-size:12px;margin-top:8px">{reason}</div>' if (not changed and reason) else ''}
    </div>

    <div style="font-size:10px;color:#555;letter-spacing:0.1em;margin-bottom:10px">▸ 전환 지표 (200일선)</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px;background:#0d0d0d;border-radius:6px">
      <tr><td style="padding:8px 14px;color:#888">S&P500 종가</td><td style="padding:8px 14px;text-align:right;color:#fff;font-family:monospace">{close:,.2f}</td></tr>
      <tr><td style="padding:8px 14px;color:#888">200일선</td><td style="padding:8px 14px;text-align:right;color:#fff;font-family:monospace">{ma200:,.2f}</td></tr>
      <tr><td style="padding:8px 14px;color:#888">이격도</td><td style="padding:8px 14px;text-align:right;font-family:monospace;color:{'#22c55e' if dev>0 else '#ef4444'}">{dev:+.2f}%</td></tr>
      <tr><td style="padding:8px 14px;color:#888">방어 전환선 (-3%)</td><td style="padding:8px 14px;text-align:right;color:#888;font-family:monospace">{ma200*DEFENSE_TRIGGER:,.2f}</td></tr>
    </table>

    <div style="font-size:10px;color:#555;letter-spacing:0.1em;margin-bottom:10px">▸ 목표 비중 대비 현황 ({phase})</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:8px">
      <tr style="border-bottom:1px solid #222">
        <td style="padding:6px 14px;color:#555;font-size:11px">자산</td>
        <td style="padding:6px 14px;color:#555;font-size:11px;text-align:right">목표</td>
        <td style="padding:6px 14px;color:#555;font-size:11px;text-align:right">현재</td>
        <td style="padding:6px 14px;color:#555;font-size:11px;text-align:right">이탈</td>
        <td style="padding:6px 14px;color:#555;font-size:11px;text-align:right">허용밴드</td>
      </tr>
      {rebal}
    </table>
    <div style="color:#666;font-size:11px;margin-bottom:24px">총 평가액 {total:,}원{f' · 미편입 자산(BRK.B·SCHD·VOO) {excluded:,}원 포함 — v4.0 배분 외, 매도 후 재배분 필요' if excluded else ''}</div>

    {f'''<div style="font-size:10px;color:#555;letter-spacing:0.1em;margin-bottom:10px">▸ ₿ BTC (별도 관리)</div>
    <div style="background:#0d0d0d;border-radius:6px;padding:12px 14px;margin-bottom:24px;color:#ccc;font-size:13px">
      {btc_bal} BTC{f' · ${btc_usd:,.0f}' if btc_usd else ''}
    </div>''' if btc_bal else ''}

    <div style="color:#444;font-size:10px;border-top:1px solid #1a1a1a;padding-top:14px">
      Portfolio System v4.0 · 26년 백테스트 CAGR 8.6% / MDD -17.6%<br>
      전환: 공격→방어(200일선 -3% 이탈) / 방어→공격(200일선 회복 + 90일 경과)
    </div>
  </div>
</body></html>"""


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

    close, ma200, dev, err = get_market_data()
    if err:
        print(f"[오류] {err}")
        send_email(f"[Portfolio] 데이터 오류 — {now}",
                   f"<html><body style='background:#070707;color:#f87171;padding:24px'>⚠️ {err}</body></html>")
        return

    prev_phase, since = load_state()
    phase, changed, reason = decide_phase(close, ma200, prev_phase, since)
    if changed:
        since = datetime.now().strftime("%Y-%m-%d")
        print(f"[전환] {prev_phase} → {phase}: {reason}")
    save_state(phase, since)

    prices, usdkrw = get_prices(list(HOLDINGS.keys()))
    rows, total, excluded = get_portfolio(phase, usdkrw, prices)
    btc_bal, btc_usd = get_btc()

    subject = f"[Portfolio v4.0] {'🔀 ' + prev_phase + '→' + phase if changed else phase} — {now}"
    html = build_html(now, close, ma200, dev, phase, changed, reason,
                      rows, total, excluded, btc_bal, btc_usd, usdkrw, None)
    send_email(subject, html)
    print(f"✅ 이메일 발송 완료 (단계: {phase}, 이격도 {dev:+.2f}%)")


if __name__ == "__main__":
    main()
