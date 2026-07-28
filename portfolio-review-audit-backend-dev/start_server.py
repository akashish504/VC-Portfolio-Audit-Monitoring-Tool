#!/usr/bin/env python
import os
import uvicorn
import sys

# Check for required packages
required_packages = ["sqlalchemy"]
missing_packages = []

for package in required_packages:
    try:
        __import__(package)
        print(f"✓ Package {package} is available")
    except ImportError:
        missing_packages.append(package)
        print(f"✗ Package {package} is missing!")

if missing_packages:
    print(f"\nWARNING: Missing required packages: {', '.join(missing_packages)}")
    print("Install them using: pip install " + " ".join(missing_packages))
    response = input("Do you want to continue anyway? (y/n): ")
    if response.lower() != 'y':
        sys.exit(1)

# Set environment variables directly within the Python process
os.environ["APP_ENV"] = "development"

print("\nEnvironment variables set:")
print(f"APP_ENV: {os.environ.get('APP_ENV')}")

# Start the server with the environment variables in the same process
if __name__ == "__main__":
    # Start the server
    print("\nStarting server at http://0.0.0.0:8001")
    uvicorn.run(
        "src.main:app", 
        host="0.0.0.0", 
        port=8001, 
        reload=True
    ) 