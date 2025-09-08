# concierge/test_exchange.py
import os, traceback
from descope import DescopeClient
from dotenv import load_dotenv
load_dotenv()
# AccessKeyLoginOptions import path may differ; check installed SDK if import fails.
try:
    from descope import AccessKeyLoginOptions
except Exception:
    try:
        from descope.models import AccessKeyLoginOptions
    except Exception:
        AccessKeyLoginOptions = None

# Environment read
PROJECT_ID = os.getenv("DESCOPE_PROJECT_ID")
ACCESS_KEY = os.getenv("NEW_DESCOPE_KEY")
AUD = os.getenv("TEST_AUD")  # set to the exact audience string you copied
SCOPES = os.getenv("TEST_SCOPES", "data:read:arxiv").split()

if not PROJECT_ID:
    print("ERROR: DESCOPE_PROJECT_ID not set")
    exit(1)
if not ACCESS_KEY:
    print("ERROR: DESCOPE_ACCESS_KEY not set")
    exit(1)
if not AUD:
    print("ERROR: TEST_AUD not set (exact audience string for inbound app)")
    exit(1)

print("Using PROJECT_ID:", PROJECT_ID)
print("Using ACCESS_KEY (first 8 chars):", ACCESS_KEY[:8] + "...")
print("Using audience:", AUD)
print("Using scopes:", SCOPES)

client = DescopeClient(project_id=PROJECT_ID)

if AccessKeyLoginOptions is None:
    print("ERROR: Could not import AccessKeyLoginOptions from descope SDK. SDK version mismatch.")
    exit(2)

try:
    # Build login options with custom_claims (aud, scope)
    login_opts = AccessKeyLoginOptions(custom_claims={"aud": AUD, "scope": " ".join(SCOPES)})
    print("Calling exchange_access_key(...) now...")
    resp = client.exchange_access_key(access_key=ACCESS_KEY, audience = AUD,login_options=login_opts)
    print("SDK response (raw):")
    print(resp)
except Exception as e:
    print("Exception raised calling exchange_access_key:")
    traceback.print_exc()
    # If exception has .response or .args, print those
    try:
        if hasattr(e, "response"):
            print("Exception.response:", e.response)
    except Exception:
        pass
    try:
        print("Exception args:", e.args)
    except Exception:
        pass
