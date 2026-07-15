import yfinance as yf
import requests
import smtplib
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

# ============================================================
# 설정 영역 — 여기만 수정
# ============================================================
EMAIL_FROM   = "ossang614@gmail.com"
EMAIL_TO     = "ossang614@gmail.com"
EMAIL_PASS   = "fuvw zbun ydje supp"
KAKAO_TOKEN  = "Rqmw5FYhctsUFOPNanK-wrCr3on3irAcAAAAAQoXIS0AAAGfMKLn4oE8pQXSEbh1"
FRED_API_KEY = "9f331b77e1bbec6e77f04a5afcbc4e75"
# ============================================================

def get_spy_data():
    spy  = yf.Ticker("SPY")
    hist = spy.history(period="1y")
    current = hist['Close'].iloc[-1]
    sma200  = hist['Close'].rolling(200).mean().iloc[-1]
    delta = hist['Close'].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss
    rsi   = (100 - (100 / (1 + rs))).iloc[-1]
    sma200_pct = ((current - sma200) / sma200) * 100
    return round(current, 2), round(sma200_pct, 2), round(rsi, 2)

def get_qqq_sma200():
    qqq  = yf.Ticker("QQQ")
    hist = qqq.history(period="1y")
    current = hist['Close'].iloc[-1]
    sma200  = hist['Close'].rolling(200).mean().iloc[-1]
    return round(((current - sma200) / sma200) * 100, 2)

def get_vix():
    vix  = yf.Ticker("^VIX")
    hist = vix.history(period="5d")
    return round(hist['Close'].iloc[-1], 2)

def get_fg():
    return None  # CNN F&G 수동 확인 필요 (stocks.cnn.com/data/fear-and-greed)

# ============================================================
# CAPE 수동 입력 — 매월 1일 multpl.com/shiller-pe 확인 후 업데이트
# ============================================================
CAPE_MANUAL = None  # 확인 후 수동 입력 (예: 41.6)
# ============================================================

def get_cape():
    return CAPE_MANUAL

def get_btc_balance():
    try:
        address = "bc1q57h8sn3ykge2yh2kn46dq5gsqn92x7pl6uanlg"
        url = f"https://blockchain.info/balance?active={address}"
        r = requests.get(url, timeout=5)
        satoshi = r.json()[address]["final_balance"]
        return round(satoshi / 100000000, 8)
    except:
        return None

def get_btc_price_krw():
    try:
        btc = yf.Ticker("BTC-USD")
        btc_usd = btc.history(period="1d")['Close'].iloc[-1]
        # USD/KRW — yfinance 실패 시 무료 API 사용
        try:
            usdkrw_t = yf.Ticker("USDKRW=X")
            krw = usdkrw_t.history(period="1d")['Close'].iloc[-1]
            if not krw or krw < 100:
                raise ValueError
        except:
            r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
            krw = r.json()["rates"]["KRW"]
        return round(btc_usd, 2), round(krw, 2), round(btc_usd * krw, 0)
    except:
        return None, None, None

def get_5day_return():
    spy  = yf.Ticker("SPY")
    hist = spy.history(period="10d")
    ret  = ((hist['Close'].iloc[-1] - hist['Close'].iloc[-6]) / hist['Close'].iloc[-6]) * 100
    return round(ret, 2)

def check_phases(sma200_pct, rsi, qqq_pct, vix, fg, ret5d, cape):
    alerts = []

    # V0
    v0_cape = cape is not None and cape >= 35
    v0_others = vix <= 18 and rsi >= 70 and (sma200_pct >= 15 or qqq_pct >= 20)

    if v0_cape and v0_others:
        alerts.append(("🔴 V0 발동", "전 자산 V0 포트폴리오로 전환 검토", "#ef4444"))
    elif v0_others and cape is not None and not v0_cape:
        alerts.append(("⚠️ V0 CAPE 확인 필수", f"VIX·RSI·이격도 조건 충족 — CAPE 수동 확인 후 V0 전환 판단\nmultpl.com/shiller-pe (현재입력값: {cape})", "#eab308"))
    elif v0_others and cape is None:
        alerts.append(("⚠️ V0 CAPE 확인 필수", "VIX·RSI·이격도 조건 충족 — CAPE 미입력\nmultpl.com/shiller-pe 확인 후 CAPE_MANUAL 업데이트 필요", "#eab308"))

    # V0.5(H)
    h1 = 0 <= sma200_pct <= 10
    h2 = fg is not None and 40 <= fg <= 60
    h3 = 14 <= vix <= 22
    h4 = 40 <= rsi <= 60
    h_count = sum([h1, h2, h3, h4])

    # F&G 미상 상태에서 나머지 조건이 2개 충족 — F&G가 결정적
    if fg is None and sum([h1, h3, h4]) == 2:
        alerts.append(("⚠️ F&G 확인 필수 (V0.5H)", "나머지 조건 2개 충족 — F&G 확인 후 V0.5(H) 전환 판단\nstocks.cnn.com/data/fear-and-greed", "#f97316"))

    if h_count >= 3:
        alerts.append((f"🟠 V0.5(H) 충족", f"{h_count}/4개 조건 충족", "#f97316"))

    # ET
    et1 = rsi <= 32
    et2 = vix >= 32
    et3 = ret5d <= -6
    et_count = sum([et1, et2, et3])
    if vix >= 40:
        alerts.append(("🚨 ET 즉시 발동", "VIX ≥ 40 단독 충족", "#dc2626"))
    elif et_count >= 2:
        alerts.append((f"🚨 ET 발동", f"{et_count}/3개 조건 충족", "#dc2626"))

    # V0.5(C) 복귀 조건: F&G ≥ 40, VIX ≤ 22, RSI ≥ 38 — F&G 결정적
    c_vix = vix <= 22
    c_rsi = rsi >= 38
    if fg is None and c_vix and c_rsi:
        alerts.append(("⚠️ F&G 확인 필수 (V0.5C)", "VIX·RSI 조건 충족 — F&G ≥ 40 확인 후 V0.5(C) 전환 판단\nstocks.cnn.com/data/fear-and-greed", "#38bdf8"))

    # V1.0 진입 조건: F&G ≥ 45 — F&G 결정적
    if fg is None and sma200_pct > 0:
        alerts.append(("⚠️ F&G 확인 필수 (V1.0)", "SMA200 상향 — F&G ≥ 45 확인 후 V1.0 진입 판단\nstocks.cnn.com/data/fear-and-greed", "#22c55e"))

    return alerts

def indicator_color(value, ok_min, ok_max):
    if ok_min <= value <= ok_max:
        return "#22c55e"
    return "#ef4444"

def build_html(now, spy_price, sma200_pct, rsi, qqq_pct, vix, fg, ret5d, alerts, cape, portfolio=None, port_total=None, usdkrw=None, current_phase=None):
    fg_str = str(fg) if fg is not None else "수동확인"

    # 지표별 상태 색상
    vix_color   = "#22c55e" if vix <= 22 else "#ef4444"
    rsi_color   = "#22c55e" if 40 <= rsi <= 60 else ("#f97316" if rsi <= 70 else "#ef4444")
    sma_color   = "#22c55e" if 0 <= sma200_pct <= 10 else ("#f97316" if sma200_pct <= 15 else "#ef4444")
    qqq_color   = "#22c55e" if qqq_pct <= 15 else ("#f97316" if qqq_pct <= 20 else "#ef4444")
    fg_color    = "#22c55e" if fg is not None and 40 <= fg <= 60 else "#f97316"
    ret_color   = "#22c55e" if ret5d >= 0 else ("#f97316" if ret5d >= -6 else "#ef4444")

    alert_rows = ""
    if alerts:
        for title, desc, color in alerts:
            alert_rows += f"""
            <tr style="background:{color}22">
              <td style="padding:10px 14px;font-weight:700;color:{color}">{title}</td>
              <td style="padding:10px 14px;color:#ccc">{desc}</td>
            </tr>"""
    else:
        alert_rows = '<tr><td colspan="2" style="padding:10px 14px;color:#22c55e;text-align:center">✅ 전환 신호 없음 — 현재 단계 유지</td></tr>'

    portfolio_rows = ""
    portfolio_footer = ""
    if portfolio:
        REBAL_CHECK_MONTHS = (1, 4, 7, 10)
        is_check_month = datetime.now().month in REBAL_CHECK_MONTHS
        breached = []
        for p in portfolio:
            band = min(5.0, p["target"] * 0.25)
            is_breach = abs(p["diff"]) > band
            if is_breach:
                breached.append(p["name"])
            row_color = "#ef4444" if is_breach else "#22c55e"
            sign = "+" if p["diff"] >= 0 else ""
            portfolio_rows += f"""
            <tr style="background:{'#1a0000' if is_breach else '#001400'}22">
              <td style="padding:8px 14px;color:#ccc;font-size:12px">{p['name']}</td>
              <td style="padding:8px 14px;color:#fff;font-family:monospace;font-size:12px;text-align:right">{p['val']:,.0f}원</td>
              <td style="padding:8px 14px;color:#888;font-family:monospace;font-size:12px;text-align:right">{p['pct']:.1f}%</td>
              <td style="padding:8px 14px;color:#888;font-family:monospace;font-size:12px;text-align:right">목표{p['target']}%</td>
              <td style="padding:8px 14px;color:{row_color};font-family:monospace;font-size:12px;text-align:right">{sign}{p['diff']}% (허용±{band:.1f}%)</td>
            </tr>"""
        if is_check_month:
            portfolio_footer = (f"⚠️ 정기 점검월 — 리밸런싱 실행 권장: {', '.join(breached)}" if breached
                                 else "✅ 정기 점검월 — 밴드 이내, 리밸런싱 불필요")
        else:
            next_month = min([m for m in REBAL_CHECK_MONTHS if m > datetime.now().month] or [REBAL_CHECK_MONTHS[0]])
            portfolio_footer = (f"👀 모니터링 중(밴드 이탈: {', '.join(breached)}) — 실행은 {next_month}월 정기 점검 시" if breached
                                 else f"👀 모니터링 중 — 다음 정기 점검: {next_month}월")
    else:
        portfolio_rows = '<tr><td colspan="5" style="padding:10px 14px;color:#ef4444;text-align:center">포트폴리오 조회 실패</td></tr>'

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="background:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;color:#e0e0e0">
  <div style="max-width:600px;margin:0 auto">

    <!-- 헤더 -->
    <div style="border-bottom:1px solid #222;padding-bottom:12px;margin-bottom:20px">
      <div style="font-size:18px;font-weight:800;color:#fff">PORTFOLIO ALERT</div>
      <div style="font-size:11px;color:#444;margin-top:4px">{now}</div>
    </div>

    <!-- 지표 현황 -->
    <div style="font-size:10px;color:#555;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">▸ 지표 현황</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
      <tr style="background:#111">
        <td style="padding:10px 14px;color:#888;font-size:12px">SPY 현재가</td>
        <td style="padding:10px 14px;font-weight:700;color:#fff;font-family:monospace">${spy_price}</td>
        <td style="padding:10px 14px;color:#888;font-size:12px">5거래일 수익률</td>
        <td style="padding:10px 14px;font-weight:700;color:{ret_color};font-family:monospace">{ret5d:+.1f}%</td>
      </tr>
      <tr style="background:#0d0d0d">
        <td style="padding:10px 14px;color:#888;font-size:12px">SMA200 이격</td>
        <td style="padding:10px 14px;font-weight:700;color:{sma_color};font-family:monospace">{sma200_pct:+.1f}%</td>
        <td style="padding:10px 14px;color:#888;font-size:12px">QQQ SMA200</td>
        <td style="padding:10px 14px;font-weight:700;color:{qqq_color};font-family:monospace">{qqq_pct:+.1f}%</td>
      </tr>
      <tr style="background:#111">
        <td style="padding:10px 14px;color:#888;font-size:12px">RSI (14)</td>
        <td style="padding:10px 14px;font-weight:700;color:{rsi_color};font-family:monospace">{rsi:.1f}</td>
        <td style="padding:10px 14px;color:#888;font-size:12px">VIX</td>
        <td style="padding:10px 14px;font-weight:700;color:{vix_color};font-family:monospace">{vix}</td>
      </tr>
      <tr style="background:#0d0d0d">
        <td style="padding:10px 14px;color:#888;font-size:12px">F&G</td>
        <td style="padding:10px 14px;font-weight:700;color:{fg_color};font-family:monospace">{fg_str}</td>
        <td style="padding:10px 14px;color:#888;font-size:12px">CAPE</td>
        <td style="padding:10px 14px;font-weight:700;color:{'#ef4444' if cape and cape >= 35 else '#22c55e'};font-family:monospace">{cape if cape else '확인필요'}</td>
      </tr>
    </table>

    <!-- 알람 -->
    <div style="font-size:10px;color:#555;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">▸ 전환 신호</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
      {alert_rows}
    </table>

    <!-- 포트폴리오 현황 -->
    <div style="font-size:10px;color:#555;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">▸ 포트폴리오 현황 ({current_phase or '확인 필요'} 기준)</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:8px">
      <tr style="background:#111">
        <td style="padding:8px 14px;color:#555;font-size:10px">종목</td>
        <td style="padding:8px 14px;color:#555;font-size:10px;text-align:right">평가액</td>
        <td style="padding:8px 14px;color:#555;font-size:10px;text-align:right">비중</td>
        <td style="padding:8px 14px;color:#555;font-size:10px;text-align:right">목표</td>
        <td style="padding:8px 14px;color:#555;font-size:10px;text-align:right">편차</td>
      </tr>
      {portfolio_rows}
    </table>
    <div style="font-size:11px;color:#888;margin-bottom:8px">총평가액: {f'{port_total:,.0f}원' if port_total else '-'} · 환율: {f'{usdkrw:,.0f}원' if usdkrw else '-'}</div>
    <div style="font-size:12px;color:#ccc;margin-bottom:24px">{portfolio_footer}</div>

    <!-- 전환 기준 -->
    <div style="font-size:10px;color:#555;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">▸ 단계별 전환 기준</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
      <tr style="background:#180808">
        <td style="padding:8px 14px;width:130px"><span style="color:#ef4444;font-weight:700">🔴 V0</span></td>
        <td style="padding:8px 14px;font-size:11px;color:#aaa">CAPE≥35 · VIX≤18 · RSI≥70 · SMA+15% OR QQQ+20% — 전부</td>
      </tr>
      <tr style="background:#180e00">
        <td style="padding:8px 14px"><span style="color:#f97316;font-weight:700">🟠 V0.5(H)</span></td>
        <td style="padding:8px 14px;font-size:11px;color:#aaa">SMA 0~+10% · F&G 40~60 · VIX 14~22 · RSI 40~60 — 4개 중 3개</td>
      </tr>
      <tr style="background:#00121a">
        <td style="padding:8px 14px"><span style="color:#38bdf8;font-weight:700">🔵 V0.5(C)</span></td>
        <td style="padding:8px 14px;font-size:11px;color:#aaa">F&G≥40 · VIX≤22 · RSI≥38 — 3개 전부 (ET 이후에만)</td>
      </tr>
      <tr style="background:#001400">
        <td style="padding:8px 14px"><span style="color:#22c55e;font-weight:700">🟢 V1.0</span></td>
        <td style="padding:8px 14px;font-size:11px;color:#aaa">V0.5(C) 경유 필수 · SMA/50주선 돌파 · F&G≥45</td>
      </tr>
      <tr style="background:#140000">
        <td style="padding:8px 14px"><span style="color:#dc2626;font-weight:700">🚨 ET</span></td>
        <td style="padding:8px 14px;font-size:11px;color:#aaa">RSI≤32 · VIX≥32 · 5일낙폭≥-6% — 2개 이상 / VIX≥40 즉시</td>
      </tr>
    </table>

    <div style="font-size:10px;color:#333;text-align:center;padding-top:12px;border-top:1px solid #1a1a1a">
      Portfolio System v3.0 · 지표 자동 수집 (매일 실행)
    </div>
  </div>
</body>
</html>
"""
    return html

def refresh_kakao_token():
    try:
        with open("kakao_tokens.txt", "r") as f:
            lines = f.read().splitlines()
            refresh_token = [l.split("=")[1] for l in lines if l.startswith("REFRESH_TOKEN")][0]
        r = requests.post(
            "https://kauth.kakao.com/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": "3dd75c05a9196195325b5df5ed668a83",
                "refresh_token": refresh_token,
            }
        )
        data = r.json()
        new_access  = data.get("access_token")
        new_refresh = data.get("refresh_token")
        if new_access:
            with open("kakao_tokens.txt", "r") as f:
                content = f.read().splitlines()
            updated = []
            for line in content:
                if line.startswith("ACCESS_TOKEN"):
                    updated.append(f"ACCESS_TOKEN={new_access}")
                elif line.startswith("REFRESH_TOKEN") and new_refresh:
                    updated.append(f"REFRESH_TOKEN={new_refresh}")
                else:
                    updated.append(line)
            with open("kakao_tokens.txt", "w") as f:
                f.write("\n".join(updated))
            return new_access
        return None
    except:
        return None

def get_kakao_token():
    try:
        with open("kakao_tokens.txt", "r") as f:
            for line in f.read().splitlines():
                if line.startswith("ACCESS_TOKEN"):
                    return line.split("=")[1]
    except:
        pass
    return KAKAO_TOKEN

def send_kakao(text):
    token = get_kakao_token()
    url   = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "template_object": json.dumps({
            "object_type": "text",
            "text": text,
            "link": {"web_url": "https://stocks.cnn.com/data/fear-and-greed"}
        })
    }
    r = requests.post(url, headers=headers, data=data)
    if r.status_code == 401:
        new_token = refresh_kakao_token()
        if new_token:
            headers["Authorization"] = f"Bearer {new_token}"
            r = requests.post(url, headers=headers, data=data)
    if r.status_code != 200:
        print(f"카카오톡 오류: {r.status_code} {r.text}")
    return r.status_code == 200

def send_email(subject, html_body):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = EMAIL_FROM
    msg['To']      = EMAIL_TO
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login(EMAIL_FROM, EMAIL_PASS)
        s.send_message(msg)

def get_portfolio_status(usdkrw, phase="V0.5(H)"):
    try:
        if not usdkrw or usdkrw < 100:
            return None, None

        # 보유 수량 (도미노.xlsx 기준 수동 입력)
        # SCHP, QQQ, PDBC: 2026-07 기준 미보유 (qty=0) — 매수 시 수량 갱신 필요
        holdings = {
            "BRK-B":     {"qty": 56,  "type": "us", "name": "BRK.B"},
            "GLD":       {"qty": 19,  "type": "us", "name": "GLD"},
            "SCHD":      {"qty": 10,  "type": "us", "name": "SCHD"},
            "SCHP":      {"qty": 0,   "type": "us", "name": "SCHP"},
            "QQQ":       {"qty": 0,   "type": "us", "name": "QQQ"},
            "PDBC":      {"qty": 0,   "type": "us", "name": "PDBC"},
            "360750.KS": {"qty": 252, "type": "kr", "name": "TIGER S&P500"},
            "458730.KS": {"qty": 466, "type": "kr", "name": "TIGER 배당다우존스"},
            "102110.KS": {"qty": 56,  "type": "kr", "name": "TIGER 200"},
        }

        # 단계별 목표 비중 (Portfolio System v3.0, SCHP 편입 반영)
        # SCHD(미국)와 458730.KS(TIGER 배당다우존스, 국내 동일지수 ETF)는
        # 동일 자산군(SCHD 슬롯)으로 SCHD_GROUP 목표를 공유함 — 별도 슬롯 아님
        TARGETS_BY_PHASE = {
            "V0":       {"BRK-B": 30, "360750.KS": 10, "SCHP": 20, "SCHD_GROUP": 10, "GLD": 15, "102110.KS": 10, "PDBC": 5},
            "V0.5(H)":  {"BRK-B": 25, "360750.KS": 25, "SCHP": 10, "SCHD_GROUP": 10, "GLD": 15, "102110.KS": 10, "PDBC": 5},
            "V0.5(C)":  {"BRK-B": 25, "360750.KS": 25, "SCHP": 10, "SCHD_GROUP": 10, "GLD": 15, "102110.KS": 10, "PDBC": 5},
            "V1.0":     {"360750.KS": 50, "QQQ": 20, "GLD": 15, "102110.KS": 10, "PDBC": 5},
            "ET":       {"BRK-B": 40, "SCHP": 35, "GLD": 15, "SCHD_GROUP": 10},
        }
        # 단계 미확정 시 잠정 V0.5(H) 기준 적용 (호출부에서 별도 경고 표시)
        targets = TARGETS_BY_PHASE.get(phase, TARGETS_BY_PHASE["V0.5(H)"])
        SCHD_GROUP_TICKERS = ("SCHD", "458730.KS")
        SCHD_GROUP_TARGET = targets.get("SCHD_GROUP", 0)

        total = 0
        values = {}
        for ticker, info in holdings.items():
            try:
                if info["qty"] == 0:
                    values[ticker] = {"val": 0, "price": 0, "info": info}
                    continue
                t = yf.Ticker(ticker)
                price = t.history(period="1d")['Close'].iloc[-1]
                if info["type"] == "us":
                    if not usdkrw or usdkrw < 100:
                        values[ticker] = {"val": 0, "price": 0, "info": info}
                        continue
                    val = price * info["qty"] * usdkrw
                else:
                    val = price * info["qty"]
                values[ticker] = {"val": val, "price": price, "info": info}
                total += val
            except:
                values[ticker] = {"val": 0, "price": 0, "info": info}

        result = []
        schd_group_val = 0
        for ticker, data in values.items():
            if ticker in SCHD_GROUP_TICKERS:
                schd_group_val += data["val"]
                continue
            name = data["info"].get("name", ticker)
            pct = round(data["val"] / total * 100, 1) if total > 0 else 0
            target = targets.get(ticker, 0)
            diff = round(pct - target, 1)
            result.append({"name": name, "pct": pct, "target": target, "diff": diff, "val": data["val"]})

        # SCHD + TIGER 배당다우존스 합산 1줄 (target 있는 단계만 표시)
        schd_pct = round(schd_group_val / total * 100, 1) if total > 0 else 0
        schd_diff = round(schd_pct - SCHD_GROUP_TARGET, 1)
        result.append({
            "name": "SCHD+배당다우",
            "pct": schd_pct,
            "target": SCHD_GROUP_TARGET,
            "diff": schd_diff,
            "val": schd_group_val
        })

        return result, total
    except:
        return None, None

def determine_phase(v0_cape, v0_others, h_count, vix, et_count, c_vix, c_rsi, fg):
    """
    당일 지표 기준 단계 판별. 이력(직전 단계) 정보 없이 계산하므로
    'ET→V0.5(C) 경유 필수', 'V0.5(H)→V1.0 직접전환 불가' 같은
    경로 의존 규칙은 반영되지 않음 — 참고용 판별이며 최종 확인은 수동으로 할 것.
    V1.0은 50주선 데이터 미수집으로 자동판별 대상에서 제외.
    """
    if vix >= 40:
        return "ET"
    if et_count >= 2:
        return "ET"
    if v0_cape and v0_others:
        return "V0"
    if h_count >= 3:
        return "V0.5(H)"
    if c_vix and c_rsi and fg is not None and fg >= 40:
        return "V0.5(C)"
    return "확인 필요"

def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{now}] 지표 수집 시작...")

    spy_price, sma200_pct, rsi = get_spy_data()
    qqq_pct = get_qqq_sma200()
    vix     = get_vix()
    fg      = get_fg()
    ret5d   = get_5day_return()
    cape    = get_cape()

    alerts = check_phases(sma200_pct, rsi, qqq_pct, vix, fg, ret5d, cape)

    # V0 판단 변수 (카카오 메시지용)

    btc_balance = get_btc_balance()
    btc_usd, usdkrw, btc_krw_unit = get_btc_price_krw()
    btc_total_krw = round(btc_balance * btc_krw_unit, 0) if btc_balance and btc_krw_unit else None

    # 현재 단계 판단 (당일 지표 기준, 이력 미반영 — determine_phase() 주석 참고)
    v0_cape = cape is not None and cape >= 35
    v0_others = vix <= 18 and rsi >= 70 and (sma200_pct >= 15 or qqq_pct >= 20)
    h1 = 0 <= sma200_pct <= 10
    h2 = fg is not None and 40 <= fg <= 60
    h3 = 14 <= vix <= 22
    h4 = 40 <= rsi <= 60
    h_count = sum([h1, h2, h3, h4])
    et1 = rsi <= 32
    et2 = vix >= 32
    et3 = ret5d <= -6
    et_count = sum([et1, et2, et3])
    c_vix = vix <= 22
    c_rsi = rsi >= 38
    current_phase = determine_phase(v0_cape, v0_others, h_count, vix, et_count, c_vix, c_rsi, fg)

    portfolio, port_total = get_portfolio_status(usdkrw, current_phase)

    if alerts:
        subject = f"[Portfolio Alert] 전환 신호 감지 — {now}"
    else:
        subject = f"[Portfolio Alert] 일일 보고 — {now}"

    html = build_html(now, spy_price, sma200_pct, rsi, qqq_pct, vix, fg, ret5d, alerts, cape, portfolio, port_total, usdkrw, current_phase)
    send_email(subject, html)

    # 카카오톡 요약 발송
    def indicator_emoji(value, green_min, green_max, yellow_min=None, yellow_max=None):
        if green_min <= value <= green_max:
            return "🟢"
        if yellow_min is not None and yellow_min <= value <= yellow_max:
            return "🟡"
        return "🔴"

    vix_e   = "🟢" if vix <= 18 else ("🟡" if vix <= 22 else "🔴")
    rsi_e   = "🟢" if 40 <= rsi <= 60 else ("🟡" if rsi <= 70 else "🔴")
    sma_e   = "🟢" if 0 <= sma200_pct <= 10 else ("🟡" if sma200_pct <= 15 else "🔴")
    qqq_e   = "🟢" if qqq_pct <= 15 else ("🟡" if qqq_pct <= 20 else "🔴")
    fg_e    = "🟢" if fg is not None and 40 <= fg <= 60 else "🔴"
    ret_e   = "🟢" if ret5d >= 0 else ("🟡" if ret5d >= -6 else "🔴")

    cape_e  = "🔴" if cape and cape >= 35 else "🟢"

    kakao_text  = f"📊 Portfolio Alert | {now}\n"
    kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"
    kakao_text += "📌 지표 현황\n"
    kakao_text += f"SPY    ${spy_price}\n"
    kakao_text += f"CAPE   {cape if cape else '확인필요'}   {cape_e}(기준 ≥35)\n"
    kakao_text += f"SMA200  {sma200_pct:+.1f}%  {sma_e}(기준 +15%)\n"
    kakao_text += f"RSI    {rsi:.1f}   {rsi_e}(기준 ≥70)\n"
    kakao_text += f"VIX    {vix}   {vix_e}(기준 ≤18)\n"
    kakao_text += f"F&G    {fg if fg else '확인필요'}     {fg_e}(기준 40~60)\n"
    kakao_text += f"QQQ    {qqq_pct:+.1f}%  {qqq_e}(기준 +20%)\n"
    kakao_text += f"5일    {ret5d:+.1f}%   {ret_e}\n"
    kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"
    kakao_text += f"현재 단계: {current_phase}\n"
    if current_phase == "확인 필요":
        kakao_text += "⚠️ 단계 미확정 — 포트폴리오 목표치는 잠정 V0.5(H) 기준\n"
    if current_phase == "V1.0":
        kakao_text += "⚠️ V1.0은 자동판별 대상 아님(수동 확인 필요)\n"
    if alerts:
        for a in alerts:
            title = a[0] if isinstance(a, tuple) else a
            kakao_text += f"⚠️ {title}\n"
    else:
        kakao_text += "✅ 전환 신호 없음\n"
    kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"
    v0_cape_s = "✅충족" if v0_cape else ("🔲미확인" if cape is None else "❌미충족")
    kakao_text += f"🔴 V0 발동 조건 (전부 충족 시)\n"
    kakao_text += f"CAPE≥35: {v0_cape_s}\n"
    kakao_text += f"VIX≤18:  현재{vix} → {'✅충족' if vix <= 18 else '❌미충족'}\n"
    kakao_text += f"RSI≥70:  현재{rsi:.1f} → {'✅충족' if rsi >= 70 else '❌미충족'}\n"
    kakao_text += f"이격도:  S&P{sma200_pct:+.1f}%/QQQ{qqq_pct:+.1f}% → {'✅충족' if sma200_pct >= 15 or qqq_pct >= 20 else '❌미충족'}\n"
    kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"
    kakao_text += f"🟠 V0.5(H) 조건 (4개 중 3개↑)\n"
    kakao_text += f"SMA 0~+10%: 현재{sma200_pct:+.1f}% → {'✅충족' if 0 <= sma200_pct <= 10 else '❌미충족'}\n"
    fg_s = "🔲미확인" if fg is None else (f"현재{fg} → " + ("✅충족" if 40 <= fg <= 60 else "❌미충족"))
    kakao_text += f"F&G 40~60: {fg_s}\n"
    kakao_text += f"VIX 14~22: 현재{vix} → {'✅충족' if 14 <= vix <= 22 else '❌미충족'}\n"
    kakao_text += f"RSI 40~60: 현재{rsi:.1f} → {'✅충족' if 40 <= rsi <= 60 else '❌미충족'}\n"
    kakao_text += f"→ {h_count}/4개 충족\n"
    kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"
    et1 = rsi <= 32
    et2 = vix >= 32
    et3 = ret5d <= -6
    et_count = sum([et1, et2, et3])
    et1_s = "🔴충족" if et1 else "✅미충족"
    et2_s = "🔴충족" if et2 else "✅미충족"
    et3_s = "🔴충족" if et3 else "✅미충족"
    kakao_text += f"🚨 ET 발동 조건 (2개↑ 시 발동)\n"
    kakao_text += f"RSI≤32:  현재{rsi:.1f} → {et1_s}\n"
    kakao_text += f"VIX≥32:  현재{vix} → {et2_s}\n"
    kakao_text += f"5일≥-6%: 현재{ret5d:+.1f}% → {et3_s}\n"
    kakao_text += f"→ {et_count}/3개 충족 — ET {'⚠️발동' if et_count >= 2 else '✅미발동'}\n"
    if datetime.now().day == 1:
        kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"
        kakao_text += "📋 CAPE 수동 업데이트 필요\n"
        kakao_text += "multpl.com/shiller-pe 확인 후\n"
        kakao_text += "CAPE_MANUAL 값 수정하세요\n"
    kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"

    # 포트폴리오 현황 — 2번째 메시지로 분리
    kakao_text2  = f"📂 포트폴리오 현황 ({current_phase} 기준) | {now}\n"
    kakao_text2 += "━━━━━━━━━━━━━━━━━━━━━\n"
    if portfolio:
        REBAL_CHECK_MONTHS = (1, 4, 7, 10)
        is_check_month = datetime.now().month in REBAL_CHECK_MONTHS
        breached = []
        for p in portfolio:
            band = min(5.0, p["target"] * 0.25)  # 5/25 룰: 절대 ±5%p 또는 목표비중의 25% 중 더 좁은 쪽
            is_breach = abs(p["diff"]) > band
            if is_breach:
                breached.append(p["name"])
            diff_e = "🔴" if is_breach else "🟢"
            sign = "+" if p["diff"] >= 0 else ""
            kakao_text2 += f"{p['name']} {p['val']:,.0f}원 ({p['pct']:.1f}%, 목표{p['target']}% {sign}{p['diff']}% / 허용±{band:.1f}%){diff_e}\n"
        kakao_text2 += f"총평가액: {port_total:,.0f}원\n"
        kakao_text2 += f"환율: {usdkrw:,.0f}원\n"
        kakao_text2 += "━━━━━━━━━━━━━━━━━━━━━\n"
        if is_check_month:
            if breached:
                kakao_text2 += f"⚠️ 정기 점검월 — 리밸런싱 실행 권장: {', '.join(breached)}\n"
            else:
                kakao_text2 += "✅ 정기 점검월 — 밴드 이내, 리밸런싱 불필요\n"
        else:
            next_month = min([m for m in REBAL_CHECK_MONTHS if m > datetime.now().month] or [REBAL_CHECK_MONTHS[0]])
            if breached:
                kakao_text2 += f"👀 모니터링 중(밴드 이탈: {', '.join(breached)}) — 실행은 {next_month}월 정기 점검 시\n"
            else:
                kakao_text2 += f"👀 모니터링 중 — 다음 정기 점검: {next_month}월\n"
    else:
        kakao_text2 += "포트폴리오 조회 실패\n"
    kakao_text2 += "━━━━━━━━━━━━━━━━━━━━━\n"
    kakao_text2 += "₿ BTC 탱젬 잔고\n"
    if btc_balance is not None:
        kakao_text2 += f"잔고:  {btc_balance} BTC\n"
        kakao_text2 += f"BTC:   ${btc_usd:,.2f}\n"
        kakao_text2 += f"평가액: {btc_total_krw:,.0f}원\n"
        if port_total and port_total > 0:
            btc_pct = round(btc_total_krw / port_total * 100, 2)
            kakao_text2 += f"메인포트폴리오 대비: {btc_pct}%\n"
        else:
            kakao_text2 += "메인포트폴리오 대비: 계산불가(포트폴리오 조회 실패)\n"
    else:
        kakao_text2 += "잔고 조회 실패\n"

    if send_kakao(kakao_text):
        print("✅ 카카오톡 1번 발송 완료")
    else:
        print("⚠️ 카카오톡 1번 발송 실패")

    if send_kakao(kakao_text2):
        print("✅ 카카오톡 2번 발송 완료")
    else:
        print("⚠️ 카카오톡 2번 발송 실패")

    print("✅ 이메일 발송 완료")

if __name__ == "__main__":
    main()
