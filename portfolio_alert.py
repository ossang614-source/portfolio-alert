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
EMAIL_PASS = "kizc werz ffzh tbhz"

# ============================================================
# 단계별 목표 비중 — 계좌별 2개 세트
#   메인(일반계좌): 코스피 비과세 활용
#   연금저축·IRP: 코스피 제외(연금 인출세 부과로 비과세 실익 없음) +
#                 IRP 규정(위험자산 70% 상한, 안전자산 30% 이상) 준수
#   전환 신호(200일선)는 세 계좌 모두 동일하게 적용
# ============================================================
TARGETS = {
    # 2026-08-19 방어배분 개정: 백테스트 결과 방어 시 주식 0%(금30/SCHP70)가
    # 현행(주식30% 유지) 대비 CAGR·MDD·2022년 성과 전부 개선되는 순수 이득으로 확인.
    # 26년(5자산): CAGR 10.0%→11.0%, MDD -17.2%→-14.5%. 30년(4자산근사): CAGR 9.9%→10.5%,
    # MDD -17.3%→-15.1%. 기존 "완전청산 손해" 결론은 BRK.B·비상스위치·최소유예 도입 이전
    # 초기 설계 기준이었음 — 누적 개선으로 결론이 뒤집힘. 공격 배분(60/40)은 8:2/7:3/6:4
    # 비교 검증 결과 위험대비효율 최적점이라 현행 유지.
    # 2026-08-25 금:SCHP 비율 재조정: 미국 장기채 금리 지속상승(재정적자 GDP6%, 부채40조달러
    # 돌파) 구조적 압박 반영. 공격(안전자산40% 내) 금20→30/SCHP20→10: CAGR11.3→11.6%,
    # MDD-14.2→-14.4%(거의동일), 2022년-5.4→-4.8%(개선). 방어(100%) 금30→40/SCHP70→60:
    # CAGR11.2→11.3%, MDD-14.5→-14.2%(개선, U자형 최저점), 2022년-5.5→-5.4%.
    # 방어는 금50%부터 MDD 급격 악화(금 자체 변동성) 확인되어 40%가 상한.
    # 2026-08-25 추가 정정: 공격 배분 내 안전자산(40%)의 금:SCHP 비율 재검증 결과,
    # 30:10보다 40:0(SCHP 완전제거)이 CAGR+0.4%p 개선/MDD -0.2%p 소폭악화로 확인.
    # 26년 데이터상 SCHP 10%p 유지 근거 약함 — 사용자 확정으로 전량 금으로 전환.
    # (방어는 여전히 40:60 유지 — 그쪽은 50%부터 MDD 급격 악화하는 명확한 U자형 최적점 존재)
    # 2026-08-25 재조정: STC를 실제 파트너로 확정 후 재검증 결과, MDD 기준으로는
    # 30:10이 40:0보다 우수(-13.7% vs -14.7%), CAGR·2022년은 40:0이 근소 우위.
    # MDD 우선 원칙에 따라 30:10으로 최종 확정.
    "공격": {"133690.KS": 25, "BRK-B": 25, "102110.KS": 10, "0072R0.KS": 30, "STC": 10},
    "방어": {"133690.KS": 0,  "BRK-B": 0,  "102110.KS": 0,  "0072R0.KS": 40, "STC": 60},
}

# 연금저축·IRP는 계좌 제약이 서로 달라 배분을 분리:
#   연금저축: 담보대출 계좌라 펀드만 가능. 장기채는 재정불신 리스크 및 회복지연기
#             취약성(24년 백테스트로 확인)이 커서 배제 — 초단기채 단일 구성.
#   IRP: 담보대출 없어 ETF 가능. 2026-08-25 TIPS→RISE 미국달러SOFR금리액티브(455960)로 교체(금리급등형 방어력 우수, 환노출형).
#   둘 다 코스피 제외(연금소득세 부과로 비과세 실익 없음), 위험자산 70% 상한(IRP 법정 기준) 준수.
PENSION_TARGETS = {
    # 연금저축은 담보대출 계좌라 펀드만 가능 — RISE 버크셔TOP10은 ETF라 편입 불가.
    # 순수 버크셔 추종 "펀드"(증권자투자신탁) 상품은 확인되지 않아 S&P500 펀드 유지.
    # 2026-08 금/안전자산 비중 조정(금15→20, 안전자산 -5%p): 미국채 신뢰도 우려 반영,
    # 메인계좌와 동일 논리. 백테스트: CAGR 8.3%→8.7%, MDD 동일, 2022년 -12.7%→-12.5%.
    # 2026-08-19 QQQ_PEN 상품 변경 및 비중조정: 신한미국나스닥100인덱스(주식-파생형)가
    # 담보대출 제한상품(개별 펀드별 담보산정, 파생형은 대출가능금액 0원)으로 확인되어
    # 한국투자GoldmanSachs미국테크(주식, 파생형아님 — 담보가능)로 교체.
    # 다만 액티브·개별종목집중(엔비디아·알파벳 등)형이라 QQQ 근사 백테스트상 MDD 최적점인
    # 15%로 비중 축소(원래 30%), 나머지는 SP500_PEN으로 흡수(25→40%).
    # 백테스트(QQQ 근사): 30%일때 CAGR10.8%/MDD-16.7% → 15%일때 CAGR10.2%/MDD-15.5%(최적).
    # 2026-08-19 방어배분 개정: 방어 시 주식 0%(금30/STC70)가 현행(주식20% 유지) 대비
    # CAGR·MDD·2022년 성과 전부 개선되는 순수 이득으로 확인(CAGR 8.4→9.0%, MDD -19.7→-14.0%).
    # 2026-08-25 금:안전자산 비율 재조정: 미국 장기채 금리 지속상승 구조적 압박 반영.
    # (메인계좌와 동일 근거로 조정. 연금저축은 IRP 70%상한 규정과 무관해 그대로 적용 가능.)
    "연금저축": {
        # 2026-08-25 재조정: 15:45가 어색해 20:40으로 정리(금30/STC10은 유지).
        "공격": {"QQQ_PEN": 20, "SP500_PEN": 40, "GOLD_PEN": 30, "STC_PEN": 10},
        "방어": {"QQQ_PEN": 0, "SP500_PEN": 0, "GOLD_PEN": 40, "STC_PEN": 60},
    },
    # IRP는 담보대출이 없어 ETF 매매 가능 — RISE 버크셔포트폴리오TOP10(국내상장 ETF) 사용.
    # 2026-08 금/안전자산 비중 조정: 백테스트 CAGR 10.5%→10.7%, MDD 동일, 2022년 -10.0%→-9.5%.
    # 2026-08-19 재정정: 세션 복원 과정에서 규정준수 수정이 유실되어 위험자산 75%로
    # 재위반 상태였음(QQQ30+BRK25+GOLD20=75%, SCHP25%). 금 20→10% 축소 요청과 함께
    # BRK 25→30%로 재조정해 규정(위험자산≤70%, SCHP≥30%) 재충족.
    # 백테스트(금10%로 축소, 여유10%p 배분처): BRK로 보낼때 최선(CAGR11.5→11.8%,
    # MDD-16.6→-18.4%, 2022년 -10.0→-9.3%로 오히려 개선). 나스닥100·균등분배는 MDD 더 악화되어 기각.
    "IRP": {
        # 공격 배분은 위험자산(QQQ+BRK+GOLD) 이미 70% 상한에 걸려있어 금 추가 확대 불가
        # (GOLD 늘리려면 QQQ·BRK를 줄여야 하나, 규정상 SCHP도 최소 30% 유지 필요해 여력 없음).
        "공격": {"QQQ_PEN": 30, "BRK_PEN": 30, "GOLD_PEN": 10, "SCHP_PEN": 30},
        # 방어(주식0%)는 70%상한과 무관 — 메인계좌와 동일 근거로 금30→40 조정.
        "방어": {"QQQ_PEN": 0, "BRK_PEN": 0, "GOLD_PEN": 40, "SCHP_PEN": 60},
    },
}
PENSION_SLOT_NAMES = {
    "QQQ_PEN":   "나스닥100/테크 상품",
    "SP500_PEN": "S&P500 펀드 (연금저축용, ETF 편입불가)",
    "BRK_PEN":   "RISE 버크셔포트폴리오TOP10(475350, ETF, IRP전용) — BRK.B 27.5%+13F상위10종목",
    "GOLD_PEN":  "금 상품",
    "SCHP_PEN":  "초단기채 ETF (실물복제, IRP는 합성상품 매매불가)",
    "STC_PEN":   "초단기채 펀드",
}
# 연금저축·IRP 보유 현황 — 상품명은 실제 계좌 상품으로 매칭해 직접 채울 것
PENSION_HOLDINGS = {
    "연금저축": {
        "QQQ_PEN":   {"value": 7692037,          "name": "한국투자GoldmanSachs미국테크(UH)C-Pe — 신규매수 필요, 담보가능(파생형아님)"},
        "SP500_PEN": {"value": 31246398,  "name": "삼성미국S&P500인덱스증권자투자신탁UH_C — 보유중, 비중확대 필요"},
        "GOLD_PEN":  {"value": 17303109, "name": "KB스타골드특별자산투자신탁C-Pe"},
        "STC_PEN":   {"value": 10329425,  "name": "NH-Amundi USD초단기채권 (비중확대 필요, 삼성/KB단기채는 매도예정)"},
        "매도대상_기타": {"value": 2066468+7692037,
                       "name": "삼성달러표시단기채권+KB글로벌단기채(→NH-Amundi로 통합)+NH-Amundi필승코리아(코스피, 매도)"},
        "현금": {"value": 0, "name": "현금"},
    },
}

# IRP: 담보대출 없어 실제 상장 ETF 매매 — 메인계좌처럼 "수량(qty)" 입력 시 자동 시세조회.
IRP_HOLDINGS = {
    "QQQ_PEN":   {"ticker": "133690.KS", "qty": 63, "name": "TIGER 미국나스닥100"},
    "BRK_PEN":   {"ticker": "475350.KS", "qty": 797, "name": "RISE 버크셔포트폴리오TOP10"},
    "GOLD_PEN":  {"ticker": "0072R0.KS", "qty": 299, "name": "TIGER KRX금현물"},
    # 2026-08-25 실제 매수 상품 확정: RISE 미국달러SOFR금리액티브(455960) — 메인계좌와 동일 상품,
    # 개인연금·퇴직연금 둘 다 가능 확인됨. 환노출형(의도적 달러 익스포저 유지).
    # 2026-08-25 재정정: RISE 미국달러SOFR금리액티브(455960)는 합성(스왑형) 상품이라
    # IRP에서 매매 불가 확인됨. 실물복제형인 TIGER 미국달러단기채권액티브(329750, 실제
    # 미국채·미국달러표시 투자등급회사채 직접보유)로 교체 — 메인계좌 STC_GROUP 예비옵션과 동일 상품.
    "SCHP_PEN":  {"ticker": "329750.KS", "qty": 878, "name": "TIGER 미국달러단기채권액티브 (실물복제)"},
}
# 매도 대상(정리 예정) — 실제 보유수량 반영, 자동 시세조회
IRP_SELL_TARGETS = {
    "273130.KS": {"qty": 0, "name": "ACE 나스닥100미국채혼합 (매도예정)"},
    "360750.KS": {"qty": 0, "name": "TIGER 미국S&P500 (→RISE버크셔로 교체, 매도예정)"},
}
IRP_CASH = 73_188

# 세액공제 목적 연간 신규 납입 계획 (매년 초 납입 가정)
PENSION_ANNUAL_CONTRIB = {
    "연금저축": 6_000_000,
    "IRP": 3_000_000,
}

# 슬롯 표시명 및 자산군 분류 (이메일 표시용)
SLOT_NAMES = {
    "133690.KS": "나스닥100 그룹 (TIGER 미국나스닥100+QQQ)",
    "360750.KS": "TIGER 미국S&P500",  # 메인계좌 목표에서 제외됨(BRK-B로 교체), 연금계좌 참고용 유지
    "BRK-B":     "버크셔 해서웨이 B주 (해외주식계좌)",
    "102110.KS": "TIGER 200 (코스피)",
    "0072R0.KS": "금 그룹 (TIGER KRX금현물+GLD)",
    "STC":       "초단기채 그룹",
}
# 자산군: (분류명, 색상) — 성격이 같은 자산끼리 묶어 위험 구조를 한눈에 보이게 함
SLOT_CLASS = {
    "133690.KS": ("주식 · 미국 성장주", "#818cf8"),
    "360750.KS": ("주식 · 미국 대형주", "#22c55e"),
    "BRK-B":     ("주식 · 미국 대형주(개별주)", "#3b82f6"),
    "102110.KS": ("주식 · 국내",        "#a78bfa"),
    "0072R0.KS": ("실물자산 · 금",      "#eab308"),
    "STC":       ("안전자산 · 초단기채", "#f472b6"),
}
# 위험/안전 구분 (요약 표시용)
RISK_SLOTS = ("133690.KS", "360750.KS", "102110.KS", "BRK-B")
SAFE_SLOTS = ("0072R0.KS", "STC")

# 전환 규칙
DEFENSE_TRIGGER = 0.97   # 공격→방어: S&P500 < 200일선 × 0.97
MIN_HOLD_DAYS   = 63     # 방어→공격 시 최소 보유 거래일(약 3개월)

STATE_FILE = "phase_state.json"

# 보유 수량 (매매 시 직접 갱신)
HOLDINGS = {
    "133690.KS": {"qty": 41,   "type": "kr", "name": "TIGER 미국나스닥100"},
    "QQQ":       {"qty": 0,   "type": "us", "name": "QQQ (해외주식계좌, 나스닥100 그룹 일부 — 비과세공제 활용, 매수량 미정)"},
    "360750.KS": {"qty": 0,   "type": "kr", "name": "TIGER 미국S&P500"},
    "102110.KS": {"qty": 19,  "type": "kr", "name": "TIGER 200"},
    "0072R0.KS": {"qty": 0,   "type": "kr", "name": "TIGER KRX금현물"},
    # GLD(미국상장) — 2026-08 매도 완료. 매도대금은 TIGER KRX금현물 등 v4.0 재배분에 사용.
    "GLD":       {"qty": 0,   "type": "us", "name": "GLD (2026-08-25 전량매도 예정 — 금현물계좌/0072R0.KS로 일원화)"},
    "455960.KS": {"qty": 202,   "type": "kr", "name": "RISE 미국달러SOFR금리액티브(합성) — 신규매수 예정, 환노출(달러익스포저 의도적 유지)"},
    "468370.KS": {"qty": 0, "type": "kr", "name": "KODEX 미국인플레이션국채액티브"},
    "329750.KS": {"qty": 0,  "type": "kr", "name": "TIGER 미국달러단기채권액티브"},
    # 정리 대상 — 2026-08 전량 매도 완료: 4.9% 대출 2,872만원 상환 +
    # 잔여는 ISA 국내상장 ETF(TIGER 미국나스닥100 등)로 재편입.
    "BRK-B":     {"qty": 13,  "type": "us", "name": "BRK.B (2026-08 재편입 결정 — 신규매수 필요, 해외주식계좌)"},
    "SCHD":      {"qty": 0, "type": "us", "name": "SCHD (매도완료)"},
    "VOO":       {"qty": 0,  "type": "us", "name": "VOO (매도완료)"},
}
# 2026-08-25 SCHP→STC(초단기채) 전환: 2022년 금리급등형 위기 방어구간 실측상
# SCHP -11.6% vs STC -3.9%(근사)로 STC가 확실히 우수해 교체. 468370.KS(TIPS류)는
# 더 이상 이 슬롯 목적에 안 맞아 제외, 원래 섞여있던 329750.KS(단기채)만 유지+SHV 추가.
# 2026-08-25 실제 매수 상품 확정: RISE 미국달러SOFR금리액티브(455960, 개인연금·퇴직연금 가능)
# 환헤지 안 된 상품(달러 익스포저 의도적 유지 — 달러패권 붕괴 가능성 낮다고 판단).
# 기존 329750.KS(TIGER 미국달러단기채권액티브)는 대체 옵션으로 그룹에 유지.
STC_GROUP = ("455960.KS", "329750.KS")
# 나스닥100 슬롯 그룹 — TIGER 미국나스닥100(국내) + QQQ(해외, 연 250만원 비과세 공제 활용 목적)
# 고정 비율 없이 보유한 만큼 합산 (BRK.B·금 그룹과 같은 취지)
NASDAQ_GROUP = ("133690.KS", "QQQ")
# 금 슬롯 그룹 — TIGER KRX금현물(국내) + GLD(해외, 연 250만원 비과세 공제 활용 목적)
# 고정 비율 없이 보유한 만큼 합산해 목표 20%에 반영 (BRK.B와 같은 취지)
GOLD_GROUP = ("0072R0.KS", "GLD")
# 2026-08-25 신규: KRX 금현물계좌에서 g단위로 직접 매수하는 실물 보유분.
# ETF(0072R0.KS)와 별개 — 공공데이터포털(data.go.kr) "금융위원회_일반상품시세정보"의
# 금시세 오퍼레이션으로 KRX 금현물(1kg시장) 실제 종가를 조회(원/g). T+1 지연(전영업일 종가,
# 익영업일 오후1시 이후 갱신)이지만 사용자 확인상 하루 지연이어도 실용적으로 충분.
# API 실패 시 GC=F(국제 금선물) 근사로 자동 폴백(단, 국내 프리미엄 약 12%p 오차 있음— 확인됨).
KRX_GOLD_API_KEY = "aucqSG%2FEW8%2FIRI1T%2BN3fvVDrTf1UmByJgw5apTl5%2FvHR5LP0ehyPbe2mZzXKtXwTVwggS1l%2BvtJ%2BPcKnmLXOIg%3D%3D"
GOLD_GRAMS_QTY = 15  # 보유 그램수 — 매수 후 이 값을 직접 갱신


def get_krx_gold_price():
    """
    공공데이터포털 금융위원회_일반상품시세정보 - 금시세 오퍼레이션으로 KRX 금현물
    종가(원/g) 조회. 엔드포인트는 유사 서비스(파생상품시세정보) 명명규칙으로 추정한
    값이라 최초 실행 시 검증 필요 — 실패하면 로그에 오류를 남기고 None 반환(자동 폴백).
    """
    try:
        url = ("https://apis.data.go.kr/1160100/service/GetGeneralProductInfoService/getGoldPriceInfo"
               f"?serviceKey={KRX_GOLD_API_KEY}&resultType=json&numOfRows=1&pageNo=1")
        r = requests.get(url, timeout=10)
        data = r.json()
        item = data["response"]["body"]["items"]["item"]
        if isinstance(item, list):
            item = item[0]
        price = float(item.get("clpr") or item.get("closePrice") or item.get("price"))
        return price
    except Exception as e:
        print(f"[경고] KRX 금시세 API 조회 실패({type(e).__name__}) — GC=F 근사로 폴백")
        return None


def get_gold_gram_value(usdkrw):
    """금현물계좌 보유분(그램)의 원화 평가액 계산. KRX 공식시세 우선, 실패 시 GC=F 근사 폴백."""
    if GOLD_GRAMS_QTY <= 0:
        return 0.0
    krw_per_gram = get_krx_gold_price()
    if krw_per_gram is not None:
        return krw_per_gram * GOLD_GRAMS_QTY
    if usdkrw is None:
        return 0.0
    try:
        h = yf.Ticker("GC=F").history(period="5d").dropna(subset=["Close"])
        if h is None or not len(h):
            return 0.0
        usd_per_oz = float(h["Close"].iloc[-1])
        krw_per_gram = usd_per_oz * usdkrw / 31.1035
        return krw_per_gram * GOLD_GRAMS_QTY
    except Exception:
        return 0.0
BTC_ADDRESS = "bc1q57h8sn3ykge2yh2kn46dq5gsqn92x7pl6uanlg"


def get_market_data():
    """
    S&P500 종가·200일선·RSI(14)·비상스위치 신호·레버리지 참고신호 조회.
    비상스위치(2026-08 신설, 26년 백테스트 근거): 방어 상태에서 200일선 회복을 기다리지
    않고 즉시 공격 전환하는 조건. 다음 중 하나 충족 시 발동:
      ① 200일선 -20% 이탈 AND 주봉 RSI 강세 다이버전스 (닷컴형 장기침체 포착: -15%→-20% 조정이 근소하게 우수, CAGR+0.1%p)
      ② 일봉 RSI(14) ≤ 20 (코로나형 급락 포착: RSI≤15 최적화 결과 20이 더 우수, 2020-02 등 13건 포착)
    백테스트: 미적용 CAGR 9.3%/MDD-17.3% → 적용 CAGR 9.9%~9.8%/MDD-17.1%(방어력 손실 없이 수익 개선).
    한계: 26년간 표본이 적어(다이버전스 4건·RSI 13건) 통계적 견고성은 제한적.

    레버리지 참고신호(2026-08 신설, 포트폴리오 배분과 무관 — 순수 알림):
      200일선 -20% 이탈 단독(RSI·다이버전스 불문). 4.9% 대출 실행을 검토할 만한 극단적
      드문 시점 참고용. 36년간 3회(2008-10, 2009-02, 2009-03) 발생, 1년후 전부 플러스
      (+17.8%~+66.8%, 평균+45.5%). RSI≤20 단독(44건, 1년후평균+14.5%, 마이너스20.5%)이나
      VIX≥35 단독(RSI≤20 필요, 10건 중 1건 실패)보다 표본은 적지만 100% 성공률.
      "이벤트성 VIX급등 무시" 조건은 코로나 등 성공사례도 함께 걸러내 근거 부족으로 기각.
      표본 3건뿐이라 통계적 한계 있음 — 포트폴리오 자동조정 아닌 참고 알림으로만 사용.
    반환: (종가, 200일선, 이격도%, RSI, 비상신호bool, 비상사유, 레버리지신호bool, 오류)
    """
    try:
        hist = yf.Ticker("^GSPC").history(period="2y")
        if hist is None or hist.empty:
            return None, None, None, None, False, None, False, "S&P500 조회 실패(응답 없음)"
        hist = hist.dropna(subset=["Close"])
        if len(hist) < 200:
            return None, None, None, None, False, None, False, f"S&P500 데이터 {len(hist)}행 — 200일 미만"

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

        leverage_signal = dev <= -20  # 레버리지 참고신호: 이격도 단독, RSI·다이버전스 불문

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

        return round(close, 2), round(ma200, 2), round(dev, 2), (round(rsi, 1) if rsi is not None else None), emergency, emg_reason, leverage_signal, None
    except Exception as e:
        return None, None, None, None, False, None, False, f"S&P500 조회 예외: {type(e).__name__}: {e}"


def get_tlt_signal():
    """
    장기채(TLT) 진입 참고신호 — 순수 알림용, 자동 배분 변경 없음.
    조건: 미국 10년물 금리가 1년 신고점을 찍은 뒤 고점 대비 15bp 이상 하락 확인.
    26년 백테스트(2003~2025, 31건): 6개월후 평균+3.2%, 승률71%, 최악-21.6%(2022년
    인상사이클 중간의 가짜신호 3연속 포함) — 주식 비상스위치(승률100%)보다 신뢰도 낮음.
    자동화하지 않고, 발동 시 연준 성명·CPI 추세 등 정성적 확인 후 소액(5~10%) 진입 검토용.
    반환: (신호bool, 현재금리, 최근1년고점, 사유)
    """
    try:
        h = yf.Ticker("^TNX").history(period="2y")
        if h is None or h.empty or len(h) < 252:
            return False, None, None, None
        h = h.dropna(subset=["Close"])
        tnx = h["Close"]
        high252 = tnx.rolling(252).max()
        cur = float(tnx.iloc[-1])
        peak = float(high252.iloc[-1])
        # 최근 252거래일 내 1년신고점 이후 15bp 이상 하락한 첫 시점인지 확인
        recent = tnx.iloc[-40:]  # 최근 약 2개월 내에서 신고점->하락 패턴 탐색
        recent_high = recent.rolling(252, min_periods=1).max()
        signal = False; reason = None
        if cur <= peak - 0.15:
            # 신고점 근처(3거래일 이내)였는지 확인해 "막 하락 시작"인 경우만 신호
            days_since_peak = None
            for i in range(len(tnx)-1, max(len(tnx)-15, 0), -1):
                if tnx.iloc[i] >= peak - 0.02:
                    days_since_peak = len(tnx)-1-i
                    break
            if days_since_peak is not None and days_since_peak <= 10:
                signal = True
                reason = f"10년물 금리 1년 신고점({peak:.2f}%) 대비 {(peak-cur)*100:.0f}bp 하락 확인 — 장기채(TLT) 진입 검토 참고신호"
        return signal, round(cur, 2), round(peak, 2), reason
    except Exception:
        return False, None, None, None


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
      공격 → 방어: 종가 < 200일선 × 0.97 AND 공격 진입 후 21일 경과
        — 2026-08-19 재검증(방어=주식0% 조건 반영): 14일보다 21일이 CAGR 0.2%p 더 우수(MDD 동일).
          사용자 직관("한번 전환했으면 3주는 유지")과 백테스트 결과가 일치해 21일로 조정.
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
            if att_days is None or att_days >= 21:
                return "방어", True, f"S&P500이 200일선 -3%({ma200*DEFENSE_TRIGGER:,.0f}) 아래로 이탈 + 최소유예 충족({att_days}일) — 방어 전환"
            return "공격", False, f"200일선 -3% 이탈했으나 최소유예 미충족({att_days}/21일) — 헛발동 방지"
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
    # STC·금 슬롯은 그룹 합산
    slot_values = {}
    for slot in targets:
        if slot == "STC":
            slot_values[slot] = sum(values.get(t, 0.0) for t in STC_GROUP)
        elif slot == "0072R0.KS":
            slot_values[slot] = sum(values.get(t, 0.0) for t in GOLD_GROUP) + get_gold_gram_value(usdkrw)
        elif slot == "133690.KS":
            slot_values[slot] = sum(values.get(t, 0.0) for t in NASDAQ_GROUP)
        else:
            slot_values[slot] = values.get(slot, 0.0)
    excluded = sum(values.get(t, 0.0) for t in ("SCHD", "VOO", "360750.KS"))
    total = sum(slot_values.values()) + excluded
    if total <= 0:
        return None, 0, excluded, "보유 자산 평가액 0 — 가격 조회 실패 추정"
    GROUP_MAP = {"STC": STC_GROUP, "0072R0.KS": GOLD_GROUP, "133690.KS": NASDAQ_GROUP}
    rows = []
    for slot, tgt in targets.items():
        cur_pct = slot_values[slot] / total * 100
        band = min(5.0, tgt * 0.25)   # 5/25룰
        tickers = GROUP_MAP.get(slot, (slot,))
        detail = [
            {"ticker": t, "name": HOLDINGS[t]["name"], "qty": HOLDINGS[t]["qty"],
             "value": round(values.get(t, 0.0))}
            for t in tickers if HOLDINGS.get(t, {}).get("qty", 0) > 0
        ]
        if slot == "0072R0.KS" and GOLD_GRAMS_QTY > 0:
            detail.append({"ticker": "GOLD_GRAM", "name": "금현물계좌(실물)",
                            "qty": GOLD_GRAMS_QTY, "unit": "g",
                            "value": round(get_gold_gram_value(usdkrw))})
        rows.append({
            "slot": slot, "target": tgt, "cur": round(cur_pct, 1),
            "diff": round(cur_pct - tgt, 1), "band": round(band, 1),
            "over": abs(cur_pct - tgt) > band,
            "value": round(slot_values[slot]),
            "detail": detail,
        })
    warn = f"시세 조회 실패: {', '.join(missing)}" if missing else None
    return rows, round(total), round(excluded), warn


def get_btc(usdkrw, main_total):
    """
    BTC 잔고 조회. 원화 환산액과, 메인계좌 총액 대비 5% 미만 여부를 함께 반환.
    5% 미만이면 추가매수 안내 문구를 포함(별도 관리 자산이라 목표비중 강제는 아님).
    반환: (BTC수량, USD단가, 원화환산액, 5%미만여부, 안내문구)
    """
    try:
        r = requests.get(f"https://blockchain.info/balance?active={BTC_ADDRESS}", timeout=10)
        bal = r.json()[BTC_ADDRESS]["final_balance"] / 1e8
        h = yf.Ticker("BTC-USD").history(period="5d").dropna(subset=["Close"])
        usd = float(h["Close"].iloc[-1]) if len(h) else None
        krw = None
        below5 = False
        note = None
        if usd is not None and usdkrw is not None:
            krw = bal * usd * usdkrw
            if main_total and main_total > 0:
                pct = krw / main_total * 100
                below5 = pct < 5.0
                if below5:
                    note = f"메인계좌 대비 {pct:.1f}% — 5% 미만, 추가매수 검토 가능"
        return bal, usd, krw, below5, note
    except Exception:
        return None, None, None, False, None


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
            "value": round(cur_val),
        })
    return rows, round(total), round(other)


def get_irp_status(phase):
    """
    IRP 계좌의 목표 대비 현황 계산 (담보대출 없어 ETF 매매 가능).
    IRP_HOLDINGS에 "수량(qty)"을 입력하면 야후 파이낸스로 실시간 시세를 조회해
    평가액을 자동 계산 — 연금저축(펀드, 수동입력)과 달리 메인계좌 방식과 동일.
    """
    targets = PENSION_TARGETS.get("IRP", {}).get(phase, {})
    tickers = [v["ticker"] for v in IRP_HOLDINGS.values()] + list(IRP_SELL_TARGETS.keys())
    prices = {}
    missing = []
    for tk in tickers:
        try:
            h = yf.Ticker(tk).history(period="5d")
            h = h.dropna(subset=["Close"]) if h is not None and not h.empty else None
            prices[tk] = float(h["Close"].iloc[-1]) if h is not None and len(h) else None
        except Exception:
            prices[tk] = None
        if prices[tk] is None:
            missing.append(tk)

    values = {}
    for slot, info in IRP_HOLDINGS.items():
        p = prices.get(info["ticker"])
        values[slot] = (p * info["qty"]) if (p is not None and info["qty"] > 0) else 0.0
    other = IRP_CASH
    for tk, info in IRP_SELL_TARGETS.items():
        p = prices.get(tk)
        other += (p * info["qty"]) if (p is not None and info["qty"] > 0) else 0.0

    total = sum(values.values()) + other
    if total <= 0:
        return None, 0, 0, "IRP 보유 자산 평가액 0 — 수량 미입력 또는 시세 조회 실패"
    rows = []
    for slot, tgt in targets.items():
        cur_val = values.get(slot, 0.0)
        cur_pct = cur_val / total * 100
        band = min(5.0, tgt * 0.25)
        info = IRP_HOLDINGS.get(slot, {})
        rows.append({
            "slot": slot, "name": f"{info.get('name','-')} · {info.get('qty',0)}주",
            "target": tgt, "cur": round(cur_pct, 1),
            "diff": round(cur_pct - tgt, 1), "band": round(band, 1),
            "over": abs(cur_pct - tgt) > band,
            "value": round(cur_val),
        })
    warn = f"IRP 시세 조회 실패: {', '.join(missing)}" if missing else None
    return rows, round(total), round(other), warn


def build_html(now, close, ma200, dev, phase, changed, reason, rows, total, excluded,
               btc_bal, btc_usd, btc_krw, btc_below5, btc_note, usdkrw, err, pension_data=None,
               leverage_signal=False, tlt_signal=False, tlt_cur=None, tlt_peak=None, tlt_reason=None):
    color = "#16a34a" if phase == "공격" else "#0284c7"
    bg_light = "#f0fdf4" if phase == "공격" else "#eff6ff"
    tgt = TARGETS[phase]
    risk_t = sum(v for k, v in tgt.items() if k in RISK_SLOTS)
    safe_t = sum(v for k, v in tgt.items() if k in SAFE_SLOTS)
    risk_c = sum(r["cur"] for r in (rows or []) if r["slot"] in RISK_SLOTS)
    safe_c = sum(r["cur"] for r in (rows or []) if r["slot"] in SAFE_SLOTS)
    def _detail_html(detail):
        if not detail:
            return '<div style="color:#cbd5e1;font-size:10px;margin-top:2px">보유 없음</div>'
        return "".join(
            f'<div style="color:#94a3b8;font-size:10px;margin-top:2px">{it["name"]} · {it["qty"]}{it.get("unit","주")} · {it["value"]:,}원</div>'
            for it in detail
        )
    rebal = "".join(
        f"""<tr style="background:{'#ffffff' if i % 2 == 0 else '#f8fafc'};border-bottom:1px solid #e2e8f0">
          <td style="padding:10px 14px;color:#1e293b;font-weight:600">{SLOT_NAMES.get(r['slot'], r['slot'])}
            <div style="color:{SLOT_CLASS.get(r['slot'],('','#64748b'))[1]};font-size:10px;margin-top:2px;font-weight:400">{SLOT_CLASS.get(r['slot'],('',''))[0]}</div>
            {_detail_html(r.get('detail'))}
          </td>
          <td style="padding:10px 14px;color:#64748b;text-align:right">{r['target']}%</td>
          <td style="padding:10px 14px;color:#0f172a;text-align:right;font-family:monospace;font-weight:700">{r['cur']}%<div style="color:#94a3b8;font-size:10px;font-weight:400">{r['value']:,}원</div></td>
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
      <div style="margin-bottom:4px">
        <span style="background:{color};color:#fff;font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px;margin-right:10px">메인계좌</span>
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

    {f'''<div style="background:#fef2f2;border:1.5px solid #dc2626;border-radius:8px;padding:16px;margin-bottom:24px">
      <div style="font-size:12px;color:#dc2626;font-weight:700;margin-bottom:6px">🔴 레버리지 참고신호 발동 (200일선 {dev:+.1f}% 이탈)</div>
      <div style="color:#7f1d1d;font-size:12px;line-height:1.6">
        36년간 3회만 발생(2008-10·2009-02·2009-03), 1년후 전부 플러스(+17.8%~+66.8%, 평균+45.5%).
        4.9% 대출 실행을 검토할 만한 극단적 시점 — 다만 표본 3건뿐이라 통계적 한계 있음.
        포트폴리오는 자동조정되지 않으며, 순수 참고 알림입니다. 순자산·감당가능 규모를 반드시 함께 고려하세요.
      </div>
    </div>''' if leverage_signal else ''}

    {f'''<div style="background:#eff6ff;border:1.5px solid #3b82f6;border-radius:8px;padding:16px;margin-bottom:24px">
      <div style="font-size:12px;color:#1d4ed8;font-weight:700;margin-bottom:6px">🔵 장기채(TLT) 진입 참고신호 발동</div>
      <div style="color:#1e3a8a;font-size:12px;line-height:1.6">
        {tlt_reason}<br>
        26년 백테스트(31건): 6개월후 평균+3.2%, 승률71%, 최악-21.6%(2022년 인상사이클 중
        가짜신호 3연속 포함) — 주식 비상스위치(승률100%)보다 신뢰도 낮음. 자동 배분 변경
        없음. 연준 성명·CPI 추세 등 정성적 확인 후 소액(5~10%) 진입 검토 권장.
      </div>
    </div>''' if tlt_signal else ''}

    {f'''<div style="font-size:11px;color:#94a3b8;letter-spacing:0.1em;margin-bottom:10px;font-weight:700">▸ ₿ BTC (별도 관리)</div>
    <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:14px;margin-bottom:24px;color:#92400e;font-size:13px;font-weight:600">
      {btc_bal} BTC{f' · ${btc_usd:,.0f}' if btc_usd else ''}{f' · {btc_krw:,.0f}원' if btc_krw else ''}
      {f'<div style="color:#b45309;font-weight:700;margin-top:6px">📈 {btc_note}</div>' if btc_below5 and btc_note else ''}
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
          <td style="padding:10px 14px;color:#0f172a;text-align:right;font-family:monospace;font-weight:700">{r['cur']}%<div style="color:#94a3b8;font-size:10px;font-weight:400">{r.get('value', 0):,}원</div></td>
          <td style="padding:10px 14px;text-align:right;font-family:monospace;font-weight:700;color:{'#dc2626' if r['over'] else '#94a3b8'}">{r['diff']:+.1f}%p</td>
        </tr>""" for i, r in enumerate(rows))
    return f"""
    <div style="margin:28px 0 10px">
      <span style="background:{acc_color};color:#fff;font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px;margin-right:10px">{account_name}</span>
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

    close, ma200, dev, rsi, emergency, emg_reason, leverage_signal, err = get_market_data()
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
            since_attack = today_str   # 공격 진입일 갱신(다음 21일 카운트 시작)
        print(f"[전환] {prev_phase} → {phase}: {reason}")
    save_state(phase, since, since_attack)

    prices, usdkrw = get_prices(list(HOLDINGS.keys()))
    rows, total, excluded, port_warn = get_portfolio(phase, usdkrw, prices)
    main_total = (total or 0) + (excluded or 0)
    btc_bal, btc_usd, btc_krw, btc_below5, btc_note = get_btc(usdkrw, main_total)

    # 연금저축·IRP는 메인과 동일한 200일선 신호(phase)로 판단, 배분만 별도
    pension_data = {"연금저축": get_pension_status("연금저축", phase)}
    irp_rows, irp_total, irp_other, irp_warn = get_irp_status(phase)
    pension_data["IRP"] = (irp_rows, irp_total, irp_other)
    if irp_warn:
        print(f"[경고] {irp_warn}")

    tlt_signal, tlt_cur, tlt_peak, tlt_reason = get_tlt_signal()

    subject = f"[Portfolio v4.0] {'🔀 ' + prev_phase + '→' + phase if changed else phase} — {now}"
    if leverage_signal:
        subject = "🔴 " + subject + " [레버리지 참고신호]"
    if tlt_signal:
        subject = "🔵 " + subject + " [장기채 참고신호]"
    html = build_html(now, close, ma200, dev, phase, changed, reason,
                      rows, total, excluded, btc_bal, btc_usd, btc_krw, btc_below5, btc_note,
                      usdkrw, port_warn, pension_data, leverage_signal,
                      tlt_signal, tlt_cur, tlt_peak, tlt_reason)
    send_email(subject, html)
    print(f"✅ 이메일 발송 완료 (단계: {phase}, 이격도 {dev:+.2f}%)")


if __name__ == "__main__":
    main()
