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
KAKAO_ENABLED = False  # 2026-08: 카카오톡 발송 중단, 이메일로만 발송
CNN_FG_URL   = "https://edition.cnn.com/markets/fear-and-greed"
CAPE_URL     = "https://www.multpl.com/shiller-pe"
FRED_API_KEY = "9f331b77e1bbec6e77f04a5afcbc4e75"
# ============================================================

def get_spy_data():
    try:
        spy  = yf.Ticker("SPY")
        hist = spy.history(period="1y")
        raw_shape = None if hist is None else hist.shape
        nan_count = None if hist is None or hist.empty else int(hist['Close'].isna().sum())
        if hist is None or hist.empty:
            return None, None, None, f"SPY history() 응답이 비어있음 (야후 API 응답 없음) | yfinance={yf.__version__}"
        if nan_count == len(hist):
            return None, None, None, f"SPY Close 전체({nan_count}/{len(hist)}행) NaN — yfinance 버전({yf.__version__}) 또는 API 문제로 추정, 업그레이드 필요"
        hist = hist.dropna(subset=['Close'])  # 당일 미체결/미반영 행(NaN) 제거
        if len(hist) < 200:
            return None, None, None, f"SPY 유효 데이터 {len(hist)}행(원본{raw_shape}, NaN{nan_count}행) — 200일 미만"
        current = hist['Close'].iloc[-1]
        sma200  = hist['Close'].rolling(200).mean().iloc[-1]
        delta = hist['Close'].diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss
        rsi   = (100 - (100 / (1 + rs))).iloc[-1]
        sma200_pct = ((current - sma200) / sma200) * 100
        if any(map(lambda x: x is None or (hasattr(x, "__ne__") and x != x), [current, sma200_pct, rsi])):
            return None, None, None, f"NaN 데이터 제거 후에도 계산 NaN(RSI 분모 0 등 edge case) — current={current}, sma200={sma200}, rsi={rsi}, yfinance={yf.__version__}"
        return round(float(current), 2), round(float(sma200_pct), 2), round(float(rsi), 2), None
    except Exception as e:
        return None, None, None, f"SPY 조회 예외: {type(e).__name__}: {e}"

def get_qqq_sma200():
    try:
        qqq  = yf.Ticker("QQQ")
        hist = qqq.history(period="1y")
        raw_shape = None if hist is None else hist.shape
        nan_count = None if hist is None or hist.empty else int(hist['Close'].isna().sum())
        if hist is None or hist.empty:
            return None, f"QQQ history() 응답이 비어있음 (야후 API 응답 없음) | yfinance={yf.__version__}"
        if nan_count == len(hist):
            return None, f"QQQ Close 전체({nan_count}/{len(hist)}행) NaN — yfinance 버전({yf.__version__}) 또는 API 문제로 추정, 업그레이드 필요"
        hist = hist.dropna(subset=['Close'])  # 당일 미체결/미반영 행(NaN) 제거
        if len(hist) < 200:
            return None, f"QQQ 유효 데이터 {len(hist)}행(원본{raw_shape}, NaN{nan_count}행) — 200일 미만"
        current = hist['Close'].iloc[-1]
        sma200  = hist['Close'].rolling(200).mean().iloc[-1]
        pct = ((current - sma200) / sma200) * 100
        if pct != pct:  # NaN check
            return None, f"QQQ NaN 데이터 제거 후에도 계산 NaN — current={current}, sma200={sma200}"
        return round(float(pct), 2), None
    except Exception as e:
        return None, f"QQQ 조회 예외: {type(e).__name__}: {e}"

def get_vix():
    vix  = yf.Ticker("^VIX")
    hist = vix.history(period="5d")
    return round(hist['Close'].iloc[-1], 2)

def get_fg():
    return None  # CNN F&G 수동 확인 필요 (edition.cnn.com/markets/fear-and-greed)

# ============================================================
# CAPE 수동 입력 — 매월 1일 multpl.com/shiller-pe 확인 후 업데이트 (www.multpl.com/shiller-pe)
# ============================================================
CAPE_MANUAL = None  # 확인 후 수동 입력 (예: 41.6)
# ============================================================

# BRK.B 주당 장부가치(BVPS) 수동 입력 — 분기 실적 발표 시 갱신
# 출처: berkshirehathaway.com 분기 보고서 (B주 기준 BVPS)
# 공식 뉴스 페이지(실적 발표 PDF): https://www.berkshirehathaway.com/news/2026news.html
# ============================================================
BRKB_BVPS_MANUAL = 337.15  # 2026 Q1(3/31 기준) 10-Q 확인, 자본총계 $727.181B ÷ 1,437,903주(A환산) ÷ 1500
# ============================================================

# BRK.B 다음 실적 발표 예정일 — 발표 후 위 BVPS 갱신 필요
# 참고: berkshirehathaway.com/news 공식 공지 또는 investing.com 확인
# ============================================================
BRKB_NEXT_EARNINGS_DATE = "2026-08-03"  # YYYY-MM-DD, 확인 후 수정. 발표 후엔 다음 분기 예상일로 갱신
# ============================================================

def check_brkb_earnings():
    """
    실적 발표 예정일 도달 여부 확인. 발표일 당일부터 BVPS를 갱신할 때까지
    (=BRKB_NEXT_EARNINGS_DATE가 다음 분기 날짜로 바뀔 때까지) 매일 알림.
    실제 발표 여부는 야후 캘린더로 자동 확인하지 않음 — 공식 발표는 수동 확인이 원칙
    (2026-08-01 대화에서 3rd party 실적일자 정보 불일치 확인된 바 있음).
    """
    try:
        if not BRKB_NEXT_EARNINGS_DATE:
            return None
        target = datetime.strptime(BRKB_NEXT_EARNINGS_DATE, "%Y-%m-%d").date()
        today = datetime.now().date()
        if today >= target:
            return f"📢 BRK.B 실적 발표 예정일({BRKB_NEXT_EARNINGS_DATE}) 도래 — berkshirehathaway.com에서 확인 후 BVPS·BRKB_NEXT_EARNINGS_DATE 갱신 필요"
        return None
    except Exception:
        return None

def get_cape():
    return CAPE_MANUAL

def check_brkb_entry():
    """
    BRK.B P/B 기반 진입 신호 판별 (5단계 시스템과 병렬·독립 적용, ET 상태일 때만 의미 — V0.25(BRK))
    단일 기준: P/B ≤ 1.30 (2026-08 단순화 — 기존 3단계(1.40/1.25/1.15) 중 중간값 채택)
    분기 지연 오차(약 3.5%, BVPS 연 11~15% 성장률 기준) 감안한 안전마진 반영값.
    BVPS는 분기별 수동 입력 필요. 미입력 시 판별 불가 반환.
    """
    try:
        if BRKB_BVPS_MANUAL is None:
            return None, None, "BVPS 미입력 — 분기보고서 확인 후 BRKB_BVPS_MANUAL 갱신 필요"
        t = yf.Ticker("BRK-B")
        hist = t.history(period="5d")
        if hist is None or hist.empty:
            return None, None, "BRK-B 가격 조회 실패"
        hist = hist.dropna(subset=['Close'])
        if hist.empty:
            return None, None, "BRK-B 유효 가격 없음"
        price = float(hist['Close'].iloc[-1])
        pb = round(price / BRKB_BVPS_MANUAL, 2)
        if pb <= 1.30:
            signal = "🟢 진입 신호 (P/B ≤ 1.30, 안전마진 반영)"
        else:
            signal = "⚪ 신호 없음 (P/B > 1.30)"
        return pb, signal, None
    except Exception as e:
        return None, None, f"P/B 계산 예외: {type(e).__name__}: {e}"

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
    try:
        spy  = yf.Ticker("SPY")
        hist = spy.history(period="10d")
        if hist is None or hist.empty:
            return None, "SPY(5일) history() 응답이 비어있음"
        hist = hist.dropna(subset=['Close'])
        if len(hist) < 6:
            return None, f"SPY(5일) 유효 데이터 {len(hist)}행 — 6일 미만이라 계산 불가"
        ret = ((hist['Close'].iloc[-1] - hist['Close'].iloc[-6]) / hist['Close'].iloc[-6]) * 100
        if ret != ret:
            return None, "SPY(5일) 계산 결과 NaN"
        return round(float(ret), 2), None
    except Exception as e:
        return None, f"SPY(5일) 조회 예외: {type(e).__name__}: {e}"

# ============================================================
# 코스피 자체 ET 독립 판별 (2026-08 신설)
# 배경: 2026년 6~7월 국내증시 대폭락(VKOSPI 사상최고 97.99) 당시,
# 미국시장(SPY/VIX) 기준 ET가 전혀 감지되지 못했던 공백을 보완하기 위함.
# 기존 5단계 전환·목표비중과는 별개의 "독립 경고 알림"으로만 작동 —
# 포트폴리오 목표비중을 자동으로 바꾸지 않음(V0.25(BRK)와 동일한 안전 원칙).
# ============================================================

# VKOSPI(코스피200 변동성지수) 수동 입력 — 무료 API로 안정적 자동수집 불가 확인됨
# 출처: 한국거래소(KRX) 또는 kr.investing.com/indices/kospi-volatility 매일 확인
KOSPI_VKOSPI_MANUAL = 71.11  # 확인 후 수동 입력 (예: 83.4)

def get_kospi_data():
    """코스피(^KS11) RSI(14)와 5거래일 누적수익률 자동 조회. VKOSPI는 미포함(수동 입력 별도)."""
    try:
        kospi = yf.Ticker("^KS11")
        hist = kospi.history(period="3mo")
        if hist is None or hist.empty:
            return None, None, "^KS11 history() 응답이 비어있음"
        hist = hist.dropna(subset=['Close'])
        if len(hist) < 15:
            return None, None, f"코스피 유효 데이터 {len(hist)}행 — 15일 미만"
        delta = hist['Close'].diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss
        rsi   = (100 - (100 / (1 + rs))).iloc[-1]
        ret5d = ((hist['Close'].iloc[-1] - hist['Close'].iloc[-6]) / hist['Close'].iloc[-6]) * 100 if len(hist) >= 6 else None
        if rsi != rsi:
            return None, None, "코스피 RSI 계산 결과 NaN"
        rsi_val = round(float(rsi), 2)
        ret5d_val = round(float(ret5d), 2) if ret5d is not None and ret5d == ret5d else None
        return rsi_val, ret5d_val, None
    except Exception as e:
        return None, None, f"코스피 조회 예외: {type(e).__name__}: {e}"

def check_kospi_et():
    """
    코스피 자체 ET 조건(독립 경고용): RSI≤32, VKOSPI≥32, 5일누적낙폭≥-6% 중 2개 이상,
    또는 VKOSPI≥40 단독 즉시. F&G는 국내 비공식 3rd party 데이터라 신뢰도 문제로 제외.
    반환: (경고문구 또는 None, 오류문구 또는 None)
    """
    rsi_kospi, ret5d_kospi, err = get_kospi_data()
    if err:
        return None, f"코스피 지표 조회 실패: {err}"
    if KOSPI_VKOSPI_MANUAL is None:
        return None, "VKOSPI 미입력 — kr.investing.com/indices/kospi-volatility 확인 후 KOSPI_VKOSPI_MANUAL 갱신 필요"

    cond_rsi = rsi_kospi is not None and rsi_kospi <= 32
    cond_vix = KOSPI_VKOSPI_MANUAL >= 32
    cond_ret = ret5d_kospi is not None and ret5d_kospi <= -6
    count = sum([cond_rsi, cond_vix, cond_ret])

    if KOSPI_VKOSPI_MANUAL >= 40:
        return f"🚨 코스피 자체 ET 조건 충족(VKOSPI {KOSPI_VKOSPI_MANUAL} 단독 ≥40) — 국내 방어 검토 필요 [RSI {rsi_kospi}, 5일 {ret5d_kospi}%]", None
    if count >= 2:
        return f"🚨 코스피 자체 ET 조건 충족(2개 이상) — RSI {rsi_kospi}(≤32:{cond_rsi}) VKOSPI {KOSPI_VKOSPI_MANUAL}(≥32:{cond_vix}) 5일 {ret5d_kospi}%(≤-6%:{cond_ret})", None
    return None, None

def check_kospi_recovery():
    """
    코스피 자체 ET→V0.5(C) 복귀 조건(독립 경고용): VKOSPI≤22 AND RSI≥38 — 2개 전부 충족.
    미국 시장 기준(F&G≥40, VIX≤22, RSI≥38)에서 신뢰도 낮은 F&G만 제외한 버전.
    반환: (복귀조건 충족 여부 True/False/None(판별불가), 상태문구, 오류문구)
    """
    rsi_kospi, ret5d_kospi, err = get_kospi_data()
    if err:
        return None, None, f"코스피 지표 조회 실패: {err}"
    if KOSPI_VKOSPI_MANUAL is None:
        return None, None, "VKOSPI 미입력 — kr.investing.com/indices/kospi-volatility 확인 후 KOSPI_VKOSPI_MANUAL 갱신 필요"

    cond_vix = KOSPI_VKOSPI_MANUAL <= 22
    cond_rsi = rsi_kospi is not None and rsi_kospi >= 38
    both = cond_vix and cond_rsi

    status = f"VKOSPI {KOSPI_VKOSPI_MANUAL}(≤22:{cond_vix}) · RSI {rsi_kospi}(≥38:{cond_rsi})"
    if both:
        return True, f"🟢 코스피 복귀 조건 충족 — {status}", None
    return False, f"⏳ 코스피 복귀 조건 미충족 — {status}", None

def check_phases(sma200_pct, rsi, qqq_pct, vix, fg, ret5d, cape):
    alerts = []

    # V0
    v0_cape = cape is not None and cape >= 35
    v0_others = vix <= 18 and rsi >= 70 and (sma200_pct >= 15 or qqq_pct >= 20)

    if v0_cape and v0_others:
        alerts.append(("🔴 V0 조건 충족(경고)", "CAPE≥35·VIX≤18·RSI≥70·이격도 전부 충족 — 극단적 과열, 배분 자동전환은 없음(2026-08 V0 단계 폐지). 대응은 수동 판단", "#ef4444"))
    elif v0_others and cape is not None and not v0_cape:
        alerts.append(("⚠️ V0 CAPE 확인 필수", f"VIX·RSI·이격도 조건 충족 — CAPE 수동 확인 후 V0 전환 판단\nwww.multpl.com/shiller-pe (현재입력값: {cape})", "#eab308"))
    elif v0_others and cape is None:
        alerts.append(("⚠️ V0 CAPE 확인 필수", "VIX·RSI·이격도 조건 충족 — CAPE 미입력\nwww.multpl.com/shiller-pe 확인 후 CAPE_MANUAL 업데이트 필요", "#eab308"))

    # V0.5(H) — 2026-08 개정: V1.0에서만 진입 가능한 단일 경로로 변경.
    # 기존 4개조건(SMA200/F&G/VIX/RSI, 3개 이상) 폐지 → V1.0 카드의 복귀조건과 동일하게 통일
    h1 = vix <= 18
    h2 = rsi >= 73
    h3 = sma200_pct >= 15
    h_count = sum([h1, h2, h3])

    if h_count >= 2:
        alerts.append((f"🟠 V0.5(H) 지표 충족", f"{h_count}/3개 조건 충족 — 단, V1.0 상태에서만 실제 전환됨(다른 단계면 차단)", "#f97316"))

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
        alerts.append(("⚠️ F&G 확인 필수 (V0.5C)", "VIX·RSI 조건 충족 — F&G ≥ 40 확인 후 V0.5(C) 전환 판단\nedition.cnn.com/markets/fear-and-greed", "#38bdf8"))

    # V1.0 진입 조건: F&G ≥ 45 — F&G 결정적
    if fg is None and sma200_pct > 0:
        alerts.append(("⚠️ F&G 확인 필수 (V1.0)", "SMA200 상향 — F&G ≥ 45 확인 후 V1.0 진입 판단\nedition.cnn.com/markets/fear-and-greed", "#22c55e"))

    return alerts

def indicator_color(value, ok_min, ok_max):
    if ok_min <= value <= ok_max:
        return "#22c55e"
    return "#ef4444"

def build_html(now, spy_price, sma200_pct, rsi, qqq_pct, vix, fg, ret5d, alerts, cape, portfolio=None, port_total=None, usdkrw=None, current_phase=None, data_errors=None, btc_balance=None, btc_usd=None, btc_total_krw=None, brkb_pb=None, brkb_signal=None, brkb_err=None, v025_alert=None, brkb_earnings_alert=None, last_phase=None, rule_note=None, kospi_et_alert=None, kospi_et_err=None, kospi_recovery_status=None, kospi_recovery_err=None):
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
            if "detail" in p:
                sub_line = p["detail"] or "미보유"
            elif "qty" in p:
                unit_price = f"${p['price']:,.2f}" if p.get("currency") == "USD" else f"{p['price']:,.0f}원"
                sub_line = f"{p['qty']}주 × {unit_price}"
            else:
                sub_line = ""
            portfolio_rows += f"""
            <tr style="background:{'#1a0000' if is_breach else '#001400'}22">
              <td style="padding:8px 14px;color:#ccc;font-size:12px">{p['name']}<br><span style="color:#555;font-size:10px">{sub_line}</span></td>
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

    btc_section = ""
    if btc_balance is not None:
        btc_pct_str = "-"
        if port_total and port_total > 0 and btc_total_krw is not None:
            btc_pct_str = f"{round(btc_total_krw / port_total * 100, 2)}%"
        btc_section = f"""
    <div style="font-size:10px;color:#555;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">▸ ₿ BTC 텐젬 잔고</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
      <tr style="background:#111">
        <td style="padding:8px 14px;color:#888;font-size:12px">잔고</td>
        <td style="padding:8px 14px;color:#fff;font-family:monospace;font-size:12px;text-align:right">{btc_balance} BTC</td>
      </tr>
      <tr>
        <td style="padding:8px 14px;color:#888;font-size:12px">BTC 가격</td>
        <td style="padding:8px 14px;color:#fff;font-family:monospace;font-size:12px;text-align:right">${btc_usd:,.2f}</td>
      </tr>
      <tr style="background:#111">
        <td style="padding:8px 14px;color:#888;font-size:12px">평가액</td>
        <td style="padding:8px 14px;color:#fff;font-family:monospace;font-size:12px;text-align:right">{btc_total_krw:,.0f}원</td>
      </tr>
      <tr>
        <td style="padding:8px 14px;color:#888;font-size:12px">메인포트폴리오 대비</td>
        <td style="padding:8px 14px;color:#eab308;font-family:monospace;font-size:12px;text-align:right">{btc_pct_str}</td>
      </tr>
    </table>"""
    else:
        btc_section = """
    <div style="font-size:10px;color:#555;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">▸ ₿ BTC 텐젬 잔고</div>
    <div style="color:#ef4444;font-size:12px;margin-bottom:24px">잔고 조회 실패</div>"""

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

    {f'''<div style="background:#1a0000;border:1px solid #7f1d1d;border-radius:6px;padding:12px 14px;margin-bottom:20px">
      <div style="color:#ef4444;font-weight:700;font-size:12px;margin-bottom:6px">🚨 데이터 조회 오류 — 아래 지표 신뢰 불가</div>
      {"".join(f'<div style="color:#fca5a5;font-size:11px;margin-top:2px">· {e}</div>' for e in data_errors)}
    </div>''' if data_errors else ''}

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
        <td style="padding:10px 14px;color:#888;font-size:12px">F&G <a href="{CNN_FG_URL}" style="color:#555;font-size:9px;text-decoration:underline">(출처)</a></td>
        <td style="padding:10px 14px;font-weight:700;color:{fg_color};font-family:monospace">{fg_str}</td>
        <td style="padding:10px 14px;color:#888;font-size:12px">CAPE <a href="{CAPE_URL}" style="color:#555;font-size:9px;text-decoration:underline">(출처)</a></td>
        <td style="padding:10px 14px;font-weight:700;color:{'#ef4444' if cape and cape >= 35 else '#22c55e'};font-family:monospace">{cape if cape else '확인필요'}</td>
      </tr>
    </table>

    <!-- 알람 -->
    <div style="font-size:10px;color:#555;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">▸ 전환 신호</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
      {alert_rows}
    </table>

    <!-- BRK.B 진입신호 (단계와 독립) -->
    <div style="font-size:10px;color:#555;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">▸ BRK.B 진입신호 (P/B 기준 · 단계와 독립 판별)</div>
    <div style="background:#0d0d0d;border-radius:6px;padding:12px 14px;margin-bottom:24px">
      {f'<span style="color:#eab308;font-size:12px">⚠️ {brkb_err}</span>' if brkb_err else f'<span style="color:#fff;font-family:monospace;font-weight:700">P/B {brkb_pb}</span> <span style="color:#ccc;font-size:12px;margin-left:8px">{brkb_signal}</span>'}
      <div style="color:#555;font-size:10px;margin-top:6px">기준: P/B ≤ 1.30 (안전마진 반영, 원기준 1.35 상당)</div>
      <div style="color:#444;font-size:9px;margin-top:2px">ET 상태일 때만 의미 있음 — V0.25(BRK) 진입 판단용</div>
      {f'<div style="color:#facc15;font-size:11px;margin-top:8px;font-weight:700">{brkb_earnings_alert}</div>' if brkb_earnings_alert else ''}
    </div>

    {f'''<div style="background:#001400;border:1px solid #14532d;border-radius:6px;padding:12px 14px;margin-bottom:24px">
      <span style="color:#22c55e;font-size:12px;font-weight:700">{v025_alert}</span>
    </div>''' if v025_alert else ''}

    <!-- 코스피 자체 ET 독립 판별 -->
    <div style="font-size:10px;color:#555;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">▸ 🇰🇷 코스피 자체 ET 판별 (단계와 독립)</div>
    <div style="background:#0d0d0d;border-radius:6px;padding:12px 14px;margin-bottom:24px">
      {f'<span style="color:#eab308;font-size:12px">⚠️ {kospi_et_err}</span>' if kospi_et_err else (f'<span style="color:#ef4444;font-size:12px;font-weight:700">{kospi_et_alert}</span>' if kospi_et_alert else '<span style="color:#22c55e;font-size:12px">✅ 코스피 ET 조건 미충족</span>')}
      {f'<div style="color:#eab308;font-size:11px;margin-top:6px">⚠️ 복귀조건: {kospi_recovery_err}</div>' if kospi_recovery_err else (f'<div style="color:#aaa;font-size:11px;margin-top:6px">{kospi_recovery_status}</div>' if kospi_recovery_status else '')}
    </div>

    <!-- 포트폴리오 현황 -->
    <div style="font-size:10px;color:#555;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">▸ 포트폴리오 현황 ({current_phase or '확인 필요'} 기준){f' — 직전: {last_phase}' if last_phase else ''}</div>
    {f'<div style="color:#38bdf8;font-size:11px;margin-bottom:10px">🔀 {rule_note}</div>' if rule_note else ''}
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
    {btc_section}

    <!-- 전환 기준 -->
    <div style="font-size:10px;color:#555;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">▸ 단계별 전환 기준</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
      <tr style="background:#180e00">
        <td style="padding:8px 14px;width:130px"><span style="color:#f97316;font-weight:700">🟠 V0.5(H)</span></td>
        <td style="padding:8px 14px;font-size:11px;color:#aaa">VIX≤18 · RSI≥73 · SMA+15% — 3개 중 2개 (V1.0에서만 진입)</td>
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
      <tr style="background:#001400">
        <td style="padding:8px 14px"><span style="color:#22c55e;font-weight:700">🟢 V0.25(BRK)</span></td>
        <td style="padding:8px 14px;font-size:11px;color:#aaa">ET 중 BRK.B P/B≤1.30 — 시장 지표와 무관, 수동 전환</td>
      </tr>
      <tr style="background:#0d0818">
        <td style="padding:8px 14px"><span style="color:#a78bfa;font-weight:700">🇰🇷 코스피 ET</span></td>
        <td style="padding:8px 14px;font-size:11px;color:#aaa">RSI≤32·VKOSPI≥32·5일낙폭≥-6% 2개 이상 — 경고만, 배분 변경 없음</td>
      </tr>
      <tr style="background:#180000">
        <td style="padding:8px 14px"><span style="color:#eab308;font-weight:700">⚠️ V0(참고)</span></td>
        <td style="padding:8px 14px;font-size:11px;color:#666">CAPE≥35 등 — 2026-08 배분전환 폐지, 경고만 유지</td>
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

def send_kakao(text, link_url=None):
    token = get_kakao_token()
    url   = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "template_object": json.dumps({
            "object_type": "text",
            "text": text,
            "link": {"web_url": link_url or CNN_FG_URL}
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

        # 보유 수량 (DOMINO 앱 2026-07-17 스크린샷 기준 갱신)
        # SCHP, QQQ, 468370: 2026-07 기준 미보유 (qty=0) — 매수 시 수량 갱신 필요
        # PDBC는 2026-07 제거됨(담보대출 불가 + SCHP로 대체) — holdings에서도 삭제
        holdings = {
            "BRK-B":     {"qty": 24,  "type": "us", "name": "BRK.B"},
            "GLD":       {"qty": 19,  "type": "us", "name": "GLD"},
            "SCHD":      {"qty": 139,  "type": "us", "name": "SCHD"},
            "SCHP":      {"qty": 0,   "type": "us", "name": "SCHP"},
            "QQQ":       {"qty": 0,   "type": "us", "name": "QQQ"},
            "VOO":       {"qty": 15,   "type": "us", "name": "VOO"},
            "360750.KS": {"qty": 0, "type": "kr", "name": "TIGER S&P500"},
            "458730.KS": {"qty": 0, "type": "kr", "name": "TIGER 배당다우존스"},
            "102110.KS": {"qty": 69,  "type": "kr", "name": "TIGER 200"},
            "468370.KS": {"qty": 917,   "type": "kr", "name": "KODEX 미국인플레이션국채액티브"},
            "SHV":       {"qty": 0,   "type": "us", "name": "SHV"},
        }

        # 단계별 목표 비중 (Portfolio System v3.0, PDBC 제거 → SCHP로 편입, 2026-07)
        # SCHD(미국)와 458730.KS(TIGER 배당다우존스, 국내 동일지수 ETF)는
        # 동일 자산군(SCHD 슬롯)으로 SCHD_GROUP 목표를 공유함 — 별도 슬롯 아님
        TARGETS_BY_PHASE = {
            # V0(원점 대기) 삭제됨(2026-08) — CAPE≥35 조건은 배분 전환 없이 경고 알림으로만 유지
            "V0.25(BRK)": {"BRK-B": 25, "SHV": 60, "GLD": 15},
            "V0.5(H)":  {"BRK-B": 25, "360750.KS": 20, "SCHP": 20, "SCHD_GROUP": 10, "GLD": 15, "102110.KS": 10},
            "V0.5(C)":  {"BRK-B": 25, "360750.KS": 20, "SCHP": 20, "SCHD_GROUP": 10, "GLD": 15, "102110.KS": 10},
            "V1.0":     {"360750.KS": 40, "QQQ": 20, "SCHP": 5, "GLD": 15, "102110.KS": 20},
            "ET":       {"SHV": 85, "GLD": 15},
        }
        # 단계 미확정 시 잠정 V0.5(H) 기준 적용 (호출부에서 별도 경고 표시)
        targets = TARGETS_BY_PHASE.get(phase, TARGETS_BY_PHASE["V0.5(H)"])
        SCHD_GROUP_TICKERS = ("SCHD", "458730.KS")
        SCHD_GROUP_TARGET = targets.get("SCHD_GROUP", 0)
        SP500_GROUP_TICKERS = ("360750.KS", "VOO")
        SP500_GROUP_TARGET = targets.get("360750.KS", 0)
        SCHP_GROUP_TICKERS = ("SCHP", "468370.KS")
        SCHP_GROUP_TARGET = targets.get("SCHP", 0)

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
        sp500_group_val = 0
        schp_group_val = 0
        for ticker, data in values.items():
            if ticker in SCHD_GROUP_TICKERS:
                schd_group_val += data["val"]
                continue
            if ticker in SP500_GROUP_TICKERS:
                sp500_group_val += data["val"]
                continue
            if ticker in SCHP_GROUP_TICKERS:
                schp_group_val += data["val"]
                continue
            name = data["info"].get("name", ticker)
            pct = round(data["val"] / total * 100, 1) if total > 0 else 0
            target = targets.get(ticker, 0)
            diff = round(pct - target, 1)
            result.append({
                "name": name, "pct": pct, "target": target, "diff": diff, "val": data["val"],
                "qty": data["info"]["qty"], "price": data["price"], "currency": "USD" if data["info"]["type"] == "us" else "KRW"
            })

        def build_group_detail(tickers):
            parts = []
            for tk in tickers:
                d = values.get(tk)
                if d is None:
                    continue
                q = d["info"]["qty"]
                p = d["price"]
                unit = "$" if d["info"]["type"] == "us" else "원"
                nm = d["info"]["name"]
                if d["info"]["type"] == "us":
                    parts.append(f"{nm} {q}주×${p:,.2f}")
                else:
                    parts.append(f"{nm} {q}주×{p:,.0f}원")
            return " + ".join(parts)

        # SCHD + TIGER 배당다우존스 합산 1줄 (target 있는 단계만 표시)
        schd_pct = round(schd_group_val / total * 100, 1) if total > 0 else 0
        schd_diff = round(schd_pct - SCHD_GROUP_TARGET, 1)
        result.append({
            "name": "SCHD+배당다우",
            "pct": schd_pct,
            "target": SCHD_GROUP_TARGET,
            "diff": schd_diff,
            "val": schd_group_val,
            "detail": build_group_detail(SCHD_GROUP_TICKERS)
        })

        # TIGER S&P500 + VOO 합산 1줄 (동일 지수 국내·미국 대체 티커)
        sp500_pct = round(sp500_group_val / total * 100, 1) if total > 0 else 0
        sp500_diff = round(sp500_pct - SP500_GROUP_TARGET, 1)
        result.append({
            "name": "S&P500(TIGER+VOO)",
            "pct": sp500_pct,
            "target": SP500_GROUP_TARGET,
            "diff": sp500_diff,
            "val": sp500_group_val,
            "detail": build_group_detail(SP500_GROUP_TICKERS)
        })

        # SCHP + KODEX 미국인플레이션국채액티브(468370) 합산 1줄
        # 468370은 SCHP가 아닌 TIP(iShares)를 담는 상품으로 확인되었으나,
        # "동일 자산군(미국 TIPS)" 기준으로 합산 처리하기로 결정됨 (2026-07)
        schp_pct = round(schp_group_val / total * 100, 1) if total > 0 else 0
        schp_diff = round(schp_pct - SCHP_GROUP_TARGET, 1)
        result.append({
            "name": "SCHP+KODEX인플레국채",
            "pct": schp_pct,
            "target": SCHP_GROUP_TARGET,
            "diff": schp_diff,
            "val": schp_group_val,
            "detail": build_group_detail(SCHP_GROUP_TICKERS)
        })

        return result, total
    except:
        return None, None

def determine_phase(v0_cape, v0_others, h_count, vix, et_count, c_vix, c_rsi, fg):
    """
    당일 지표 기준 1차(원시) 단계 판별. 이 결과는 이력을 모르는 상태의 '후보'이며,
    실제 적용 전 apply_transition_rules()에서 경로의존 규칙(ET→V0.5(C) 경유 필수,
    V0.5(H)는 V1.0에서만 진입 가능 등)이 적용된다.
    V1.0은 50주선 데이터 미수집으로 자동판별 대상에서 제외.
    V0(원점 대기)는 2026-08 폐지 — CAPE≥35 등 조건은 check_phases()의 경고 알림으로만 유지되며
    더 이상 배분을 전환하지 않음. v0_cape/v0_others 인자는 하위호환을 위해 남겨두되 미사용.
    h_count는 2026-08부터 3개 조건(VIX≤18/RSI≥73/SMA200+15%) 중 충족 개수 — 2개 이상이면 후보.
    """
    if vix >= 40:
        return "ET"
    if et_count >= 2:
        return "ET"
    if h_count >= 2:
        return "V0.5(H)"
    if c_vix and c_rsi and fg is not None and fg >= 40:
        return "V0.5(C)"
    return "확인 필요"

PHASE_STATE_FILE = "phase_state.json"

def load_phase_state():
    """직전 실행의 확정 단계를 로컬 파일에서 로드. 파일 없으면(최초 실행) None."""
    try:
        with open(PHASE_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("last_phase")
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def save_phase_state(phase):
    """확정된 현재 단계를 다음 실행이 읽을 수 있도록 저장."""
    try:
        with open(PHASE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_phase": phase, "updated": datetime.now().strftime("%Y-%m-%d %H:%M")}, f, ensure_ascii=False)
    except Exception as e:
        print(f"[경고] phase_state.json 저장 실패: {e}")

def apply_transition_rules(raw_phase, last_phase):
    """
    경로의존 전환 규칙 적용.
    - ET → V0.5(C) 경유 필수: 직전이 ET였다면, 오늘 지표가 V0.5(H) 조건을 충족해도
      V0.5(C)로 강제 라우팅 (V0.5(H) 직행 금지)
    - V0.5(H)는 V1.0에서만 진입 가능(2026-08 순환구조: ET→V0.5(C)→V1.0→V0.5(H)→ET):
      직전 단계가 V1.0이 아니면, 오늘 지표가 V0.5(H) 조건(VIX≤18/RSI≥73/SMA200+15%, 2/3)을
      충족해도 전환을 차단하고 "확인 필요"로 유지
    반환: (적용된_단계, 규칙_적용_메모 또는 None)
    """
    if last_phase == "ET" and raw_phase == "V0.5(H)":
        return "V0.5(C)", f"ET→V0.5(C) 경유 규칙 적용: 오늘 지표는 V0.5(H) 조건 충족이나 직전 단계가 ET였으므로 V0.5(C)로 라우팅"
    if raw_phase == "V0.5(H)" and last_phase != "V1.0":
        return "확인 필요", f"V0.5(H)는 V1.0에서만 진입 가능 — 오늘 지표는 조건 충족이나 직전 단계가 '{last_phase}'(V1.0 아님)이므로 전환 차단"
    return raw_phase, None

def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{now}] 지표 수집 시작...")

    data_errors = []

    spy_price, sma200_pct, rsi, spy_err = get_spy_data()
    if spy_err:
        data_errors.append(f"SPY/RSI/SMA200: {spy_err}")
        print(f"[경고] {spy_err}")
        spy_price, sma200_pct, rsi = 0, 0, 0  # 계산 크래시 방지용 안전값 (알림에는 오류로 별도 표시)

    qqq_pct, qqq_err = get_qqq_sma200()
    if qqq_err:
        data_errors.append(f"QQQ SMA200: {qqq_err}")
        print(f"[경고] {qqq_err}")
        qqq_pct = 0
    vix     = get_vix()
    fg      = get_fg()
    ret5d, ret5d_err = get_5day_return()
    if ret5d_err:
        data_errors.append(f"5거래일 수익률: {ret5d_err}")
        print(f"[경고] {ret5d_err}")
        ret5d = 0
    cape    = get_cape()
    brkb_pb, brkb_signal, brkb_err = check_brkb_entry()
    brkb_earnings_alert = check_brkb_earnings()
    kospi_et_alert, kospi_et_err = check_kospi_et()
    kospi_recovery_ok, kospi_recovery_status, kospi_recovery_err = check_kospi_recovery()

    alerts = check_phases(sma200_pct, rsi, qqq_pct, vix, fg, ret5d, cape)

    # V0 판단 변수 (카카오 메시지용)

    btc_balance = get_btc_balance()
    btc_usd, usdkrw, btc_krw_unit = get_btc_price_krw()
    btc_total_krw = round(btc_balance * btc_krw_unit, 0) if btc_balance and btc_krw_unit else None

    # 현재 단계 판단 (당일 지표 기준, 이력 미반영 — determine_phase() 주석 참고)
    v0_cape = cape is not None and cape >= 35
    v0_others = vix <= 18 and rsi >= 70 and (sma200_pct >= 15 or qqq_pct >= 20)
    # V0.5(H) — 2026-08 개정: V1.0 복귀 기준(VIX≤18/RSI≥73/SMA200+15%, 3개 중 2개)과 통일
    h1 = vix <= 18
    h2 = rsi >= 73
    h3 = sma200_pct >= 15
    h_count = sum([h1, h2, h3])
    et1 = rsi <= 32
    et2 = vix >= 32
    et3 = ret5d <= -6
    et_count = sum([et1, et2, et3])
    c_vix = vix <= 22
    c_rsi = rsi >= 38
    raw_phase = determine_phase(v0_cape, v0_others, h_count, vix, et_count, c_vix, c_rsi, fg)
    last_phase = load_phase_state()
    current_phase, rule_note = apply_transition_rules(raw_phase, last_phase)
    if rule_note:
        print(f"[규칙적용] {rule_note}")

    v025_alert = None
    if current_phase == "ET" and brkb_signal and "신호 없음" not in brkb_signal:
        v025_alert = f"🟢 V0.25(BRK) 진입 검토 가능 — BRK.B {brkb_signal} (ET 유지 중, 수동 확인 후 전환)"

    # "확인 필요"인 날은 직전 확정 단계를 덮어쓰지 않음 — 경로의존 규칙(last_phase)이
    # 모호한 날 때문에 유실되지 않도록 방지
    if current_phase != "확인 필요":
        save_phase_state(current_phase)

    portfolio, port_total = get_portfolio_status(usdkrw, current_phase)

    if alerts:
        subject = f"[Portfolio Alert] 전환 신호 감지 — {now}"
    else:
        subject = f"[Portfolio Alert] 일일 보고 — {now}"

    html = build_html(now, spy_price, sma200_pct, rsi, qqq_pct, vix, fg, ret5d, alerts, cape, portfolio, port_total, usdkrw, current_phase, data_errors, btc_balance, btc_usd, btc_total_krw, brkb_pb, brkb_signal, brkb_err, v025_alert, brkb_earnings_alert, last_phase, rule_note, kospi_et_alert, kospi_et_err, kospi_recovery_status, kospi_recovery_err)
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
    if data_errors:
        kakao_text += "🚨 데이터 조회 오류 — 아래 지표 신뢰 불가\n"
        for e in data_errors:
            kakao_text += f"  · {e}\n"
        kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"
    kakao_text += "📌 지표 현황\n"
    kakao_text += f"SPY    {'오류' if spy_err else f'${spy_price}'}\n"
    kakao_text += f"CAPE   {cape if cape else '확인필요'}   {cape_e}(기준 ≥35)\n"
    kakao_text += f"SMA200  {'오류' if spy_err else f'{sma200_pct:+.1f}%'}  {sma_e}(기준 +15%)\n"
    kakao_text += f"RSI    {'오류' if spy_err else f'{rsi:.1f}'}   {rsi_e}(기준 ≥70)\n"
    kakao_text += f"VIX    {vix}   {vix_e}(기준 ≤18)\n"
    kakao_text += f"F&G    {fg if fg else '확인필요'}     {fg_e}(기준 40~60)\n"
    kakao_text += f"QQQ    {'오류' if qqq_err else f'{qqq_pct:+.1f}%'}  {qqq_e}(기준 +20%)\n"
    kakao_text += f"5일    {'오류' if ret5d_err else f'{ret5d:+.1f}%'}   {ret_e}\n"
    kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"
    kakao_text += f"현재 단계: {current_phase}\n"
    if last_phase:
        kakao_text += f"(직전 확정 단계: {last_phase})\n"
    if rule_note:
        kakao_text += f"🔀 {rule_note}\n"
    if current_phase == "확인 필요":
        kakao_text += "⚠️ 단계 미확정 — 포트폴리오 목표치는 잠정 V0.5(H) 기준\n"
    if current_phase == "V1.0":
        kakao_text += "⚠️ V1.0은 자동판별 대상 아님(수동 확인 필요)\n"
    if v025_alert:
        kakao_text += f"{v025_alert}\n"
    if alerts:
        for a in alerts:
            title = a[0] if isinstance(a, tuple) else a
            kakao_text += f"⚠️ {title}\n"
    else:
        kakao_text += "✅ 전환 신호 없음\n"
    kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"
    kakao_text += "📈 BRK.B 진입신호 (단계와 독립 판별)\n"
    if brkb_err:
        kakao_text += f"⚠️ {brkb_err}\n"
    else:
        kakao_text += f"P/B {brkb_pb} — {brkb_signal}\n"
    if brkb_earnings_alert:
        kakao_text += f"{brkb_earnings_alert}\n"
    kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"
    kakao_text += "🇰🇷 코스피 자체 ET 판별 (단계와 독립)\n"
    if kospi_et_err:
        kakao_text += f"⚠️ {kospi_et_err}\n"
    elif kospi_et_alert:
        kakao_text += f"{kospi_et_alert}\n"
    else:
        kakao_text += "✅ 코스피 ET 조건 미충족\n"
    if kospi_recovery_err:
        kakao_text += f"⚠️ 복귀조건: {kospi_recovery_err}\n"
    elif kospi_recovery_status:
        kakao_text += f"{kospi_recovery_status}\n"
    kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"
    v0_cape_s = "✅충족" if v0_cape else ("🔲미확인" if cape is None else "❌미충족")
    kakao_text += f"⚠️ V0 조건 (참고 경고, 배분전환 없음 · 2026-08 폐지)\n"
    kakao_text += f"CAPE≥35: {v0_cape_s}\n"
    kakao_text += f"VIX≤18:  현재{vix} → {'✅충족' if vix <= 18 else '❌미충족'}\n"
    kakao_text += f"RSI≥70:  현재{rsi:.1f} → {'✅충족' if rsi >= 70 else '❌미충족'}\n"
    kakao_text += f"이격도:  S&P{sma200_pct:+.1f}%/QQQ{qqq_pct:+.1f}% → {'✅충족' if sma200_pct >= 15 or qqq_pct >= 20 else '❌미충족'}\n"
    kakao_text += "━━━━━━━━━━━━━━━━━━━━━\n"
    kakao_text += f"🟠 V0.5(H) 조건 (3개 중 2개↑, V1.0에서만 실제 진입)\n"
    kakao_text += f"VIX≤18:  현재{vix} → {'✅충족' if vix <= 18 else '❌미충족'}\n"
    kakao_text += f"RSI≥73:  현재{rsi:.1f} → {'✅충족' if rsi >= 73 else '❌미충족'}\n"
    kakao_text += f"SMA+15%: 현재{sma200_pct:+.1f}% → {'✅충족' if sma200_pct >= 15 else '❌미충족'}\n"
    kakao_text += f"→ {h_count}/3개 충족\n"
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
        kakao_text += "www.multpl.com/shiller-pe 확인 후\n"
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
            if "detail" in p:
                sub_line = p["detail"] or "미보유"
            elif "qty" in p:
                unit_price = f"${p['price']:,.2f}" if p.get("currency") == "USD" else f"{p['price']:,.0f}원"
                sub_line = f"{p['qty']}주 × {unit_price}"
            else:
                sub_line = ""
            kakao_text2 += f"{p['name']} {p['val']:,.0f}원 ({p['pct']:.1f}%, 목표{p['target']}% {sign}{p['diff']}% / 허용±{band:.1f}%){diff_e}\n"
            if sub_line:
                kakao_text2 += f"  └ {sub_line}\n"
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

    if KAKAO_ENABLED:
        if send_kakao(kakao_text, link_url=CNN_FG_URL):
            print("✅ 카카오톡 1번 발송 완료")
        else:
            print("⚠️ 카카오톡 1번 발송 실패")

        if send_kakao(kakao_text2, link_url=CAPE_URL):
            print("✅ 카카오톡 2번 발송 완료")
        else:
            print("⚠️ 카카오톡 2번 발송 실패")
    else:
        print("ℹ️ 카카오톡 발송 비활성화(KAKAO_ENABLED=False) — 이메일만 발송")

    print("✅ 이메일 발송 완료")

if __name__ == "__main__":
    main()
