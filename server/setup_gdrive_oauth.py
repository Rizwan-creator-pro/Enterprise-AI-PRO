"""
Enterprise AI Hub - Google Drive OAuth2 Authorization Helper
Enables full 5TB personal Google One drive upload support.
"""
import os
import sys

def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("[ERROR] google-auth-oauthlib not installed. Run: pip install google-auth-oauthlib")
        sys.exit(1)

    scopes = ["https://www.googleapis.com/auth/drive"]
    token_file = "gdrive_oauth_token.json"

    # Check for client_secret.json
    client_secret_candidates = ["client_secret.json", "credentials.json", "oauth_credentials.json"]
    client_secret_file = None
    for cand in client_secret_candidates:
        if os.path.exists(cand):
            client_secret_file = cand
            break

    if not client_secret_file:
        print("[!] OAuth Client Credentials file not found.")
        print("To authorize your personal 5TB Google Account:")
        print("1. Open: https://console.cloud.google.com/apis/credentials?project=enterprise-ai-storage")
        print("2. Click 'Create Credentials' -> 'OAuth client ID'")
        print("3. Application type: 'Desktop app' -> Name: 'Enterprise AI'")
        print("4. Download JSON and save as 'client_secret.json' in this directory.")
        print("5. Run this script again: python setup_gdrive_oauth.py")
        sys.exit(1)

    print(f"[*] Found credentials: {client_secret_file}")
    print("[*] Launching browser for one-time Google Account login...")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, scopes=scopes)
        creds = flow.run_local_server(port=0)

        with open(token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        print(f"\n[SUCCESS] Token saved to '{token_file}'!")
        print("Your 5TB Google Drive personal quota is now fully active!")
    except Exception as e:
        print(f"[ERROR] Authorization failed: {e}")

if __name__ == "__main__":
    main()
