import requests

REST_API_KEY = "3dd75c05a9196195325b5df5ed668a83"
REDIRECT_URI = "https://localhost:5000"

# 1단계 — 인증 URL 생성
auth_url = (
    f"https://kauth.kakao.com/oauth/authorize"
    f"?client_id={REST_API_KEY}"
    f"&redirect_uri={REDIRECT_URI}"
    f"&response_type=code"
    f"&scope=talk_message"
)
print("아래 URL을 브라우저에서 열어주세요:")
print(auth_url)

# 2단계 — 리다이렉트 URL 입력
redirected_url = input("리다이렉트된 URL을 붙여넣으세요: ")
code = redirected_url.split("code=")[1].split("&")[0]

# 3단계 — 토큰 발급
r = requests.post(
    "https://kauth.kakao.com/oauth/token",
    data={
        "grant_type": "authorization_code",
        "client_id": REST_API_KEY,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }
)
data = r.json()
print("\n전체 응답:", data)
print("\n액세스 토큰:", data.get("access_token"))
print("리프레시 토큰:", data.get("refresh_token"))
print("액세스 토큰 만료:", data.get("expires_in"), "초")
print("리프레시 토큰 만료:", data.get("refresh_token_expires_in"), "초")

# 4단계 — 파일 저장
with open("kakao_tokens.txt", "w") as f:
    f.write(f"ACCESS_TOKEN={data.get('access_token')}\n")
    f.write(f"REFRESH_TOKEN={data.get('refresh_token')}\n")
print("\n토큰이 kakao_tokens.txt에 저장됐습니다.")
