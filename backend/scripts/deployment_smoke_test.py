import sys
import urllib.parse
import urllib.request

def validate_url(url_str):
    parsed = urllib.parse.urlparse(url_str)
    if parsed.scheme not in ("http", "https"):
        return False, f"URL scheme must be http or https, got '{parsed.scheme}'"
    if not parsed.netloc:
        return False, "URL netloc/host is missing"
    if "@" in parsed.netloc or parsed.username or parsed.password:
        return False, "Credentials are not allowed in the base URL"
    return True, parsed

def run_smoke_test():
    if len(sys.argv) < 2:
        print("Usage: python deployment_smoke_test.py <base-url>")
        sys.exit(1)
        
    base_url = sys.argv[1].rstrip("/")
    
    # URL Validation
    is_valid, res = validate_url(base_url)
    if not is_valid:
        print(f"FAIL: {res}")
        sys.exit(1)
        
    endpoints = [
        "/api/health",
        "/api/sentinel/status",
        "/api/sentinel/hazards",
        "/api/sentinel/world-model",
        "/api/sentinel/demo-replay"
    ]
    
    failures = 0
    for ep in endpoints:
        url = base_url + ep
        try:
            req = urllib.request.Request(url)
            # Use a bounded timeout of 5 seconds
            with urllib.request.urlopen(req, timeout=5) as response:
                status_code = response.getcode()
                if status_code == 200:
                    print(f"PASS: GET {ep} returned HTTP 200")
                else:
                    print(f"FAIL: GET {ep} returned HTTP {status_code}")
                    failures += 1
        except Exception as e:
            print(f"FAIL: GET {ep} failed with exception: {type(e).__name__}")
            failures += 1
            
    if failures > 0:
        print(f"\nSmoke test FAILED with {failures} endpoint failure(s).")
        sys.exit(1)
    else:
        print("\nSmoke test PASSED successfully.")
        sys.exit(0)

if __name__ == "__main__":
    run_smoke_test()
