import os
import sys

# Set Google OAuth credentials
os.environ["GOOGLE_CLIENT_ID"] = "277227310699-v67f0o8o9igkmlu2n3noboljfrojuv28.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_SECRET"] = "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"
os.environ["JWT_SECRET_KEY"] = "development_secret_key_for_jwt"
os.environ["SESSION_SECRET_KEY"] = "4e1a98a67515a8255ce7bc62961772855b85e6c669905e26ea2a54806de67ce9"
os.environ["APP_ENV"] = "development"

print("Environment variables set:")
print("GOOGLE_CLIENT_ID: <set>")
print("GOOGLE_CLIENT_SECRET: <set>")
print("SESSION_SECRET_KEY: <set>")
print("JWT_SECRET_KEY: <set>")
print(f"APP_ENV: {os.environ.get('APP_ENV')}")

# Execute the command passed as arguments
if len(sys.argv) > 1:
    import subprocess
    cmd = sys.argv[1:]
    print(f"Running command: {' '.join(cmd)}")
    subprocess.run(cmd) 