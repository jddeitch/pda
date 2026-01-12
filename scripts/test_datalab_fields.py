#!/usr/bin/env python3
"""Test Datalab API to see all response fields including quality scores."""

import requests
import os
import time
import json

API_KEY = os.environ.get("DATALAB_API_KEY", "-vPPAEwkoYbtFa9oa6cQRV1Gef8O1LaTSha-TZq5Yso")
headers = {"X-API-Key": API_KEY}

pdf_path = "intake/articles/Pathological-Demand-Avoidance-Syndrome (very early article by Newson).pdf"

with open(pdf_path, "rb") as f:
    files = {"file": (os.path.basename(pdf_path), f, "application/pdf")}
    data = {"output_format": "html", "mode": "accurate"}

    print("Submitting...")
    response = requests.post("https://www.datalab.to/api/v1/marker", files=files, data=data, headers=headers)
    result = response.json()
    print("Submit keys:", list(result.keys()))

if not result.get("success"):
    print("Error:", result)
else:
    check_url = result.get("request_check_url")
    print("Polling (max 3 min)...")

    for i in range(60):  # 60 * 3s = 3 minutes
        time.sleep(3)
        check_response = requests.get(check_url, headers=headers)
        status_result = check_response.json()
        status = status_result.get("status")

        if i % 10 == 0:
            print(f"Status: {status} ({i*3}s)")

        if status == "complete":
            print()
            print("=== ALL RESPONSE FIELDS ===")
            for key in sorted(status_result.keys()):
                value = status_result[key]
                if key in ("html", "markdown", "images"):
                    print(f"{key}: <{len(str(value))} chars>")
                else:
                    print(f"{key}: {value}")

            # Save non-large fields to JSON for inspection
            safe_result = {k: v for k, v in status_result.items()
                          if k not in ("html", "markdown", "images")}
            with open("/tmp/datalab_response.json", "w") as out:
                json.dump(safe_result, out, indent=2)
            print("\nSaved metadata to /tmp/datalab_response.json")
            break
        elif status == "failed":
            print("Failed:", status_result)
            break
    else:
        print("Timeout - still processing")
