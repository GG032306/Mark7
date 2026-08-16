import sys
from upstox_totp import UpstoxTOTP
from pydantic import SecretStr

# ==========================================
# 🛑 MONKEY PATCH: THE INTERCEPTOR
# Upstox removed the 'poa' field, causing validation crashes.
# This intercepts the token directly from the HTTP layer.
# ==========================================

# 1. Intercept curl_cffi (Primary engine for upstox-totp)
try:
    from curl_cffi.requests.models import Response as CurlResponse
    original_curl_json = CurlResponse.json
    def intercept_curl_json(self, **kwargs):
        parsed = original_curl_json(self, **kwargs)
        if isinstance(parsed, dict):
            payload = parsed.get("data", parsed)
            if isinstance(payload, dict) and "access_token" in payload:
                with open('access_token.txt', 'w') as f:
                    f.write(payload["access_token"])
                print("✅ SUCCESS! Token intercepted directly from network payload (curl_cffi)!")
                sys.exit(0) # Exit cleanly before Pydantic can crash
        return parsed
    CurlResponse.json = intercept_curl_json
except ImportError:
    pass

# 2. Intercept standard requests (Fallback engine)
try:
    import requests
    original_req_json = requests.models.Response.json
    def intercept_req_json(self, **kwargs):
        parsed = original_req_json(self, **kwargs)
        if isinstance(parsed, dict):
            payload = parsed.get("data", parsed)
            if isinstance(payload, dict) and "access_token" in payload:
                with open('access_token.txt', 'w') as f:
                    f.write(payload["access_token"])
                print("✅ SUCCESS! Token intercepted directly from network payload (requests)!")
                sys.exit(0) # Exit cleanly before Pydantic can crash
        return parsed
    requests.models.Response.json = intercept_req_json
except ImportError:
    pass
# ==========================================

def generate_daily_token():
    print("="*50)
    print("🤖 MARK 7: ZERO-TOUCH AUTHENTICATION")
    print("="*50)
    
    upx = UpstoxTOTP(
        username="9480281651",
        password=SecretStr("230306"), 
        pin_code=SecretStr("230306"),
        totp_secret=SecretStr("KPLYTWFMICVIRXESOICMF3I5AKZGUMSH"), 
        client_id="3c36e737-20f8-462c-9e5a-5ec2f3b841c3",
        client_secret=SecretStr("ief6o3k53k"),
        redirect_uri="https://darling-repose-tamale.ngrok-free.dev/callback"
    )
    
    try:
        # The script will successfully exit during this call due to our interceptor!
        response = upx.app_token.get_access_token()
    except Exception as e:
        print(f"❌ Error during auto-login: {e}")

if __name__ == "__main__":
    generate_daily_token()
