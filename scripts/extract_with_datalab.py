#!/usr/bin/env python3
"""
Extract PDFs to HTML using Datalab Marker API.

Processes all PDFs in intake/articles/ and saves HTML to cache/articles/.
Uses 'accurate' mode for best quality on academic papers.
"""

import os
import re
import time
import requests
from pathlib import Path

# Configuration
API_KEY = os.environ.get("DATALAB_API_KEY")
API_URL = "https://www.datalab.to/api/v1/marker"
INTAKE_DIR = Path(__file__).parent.parent / "intake" / "articles"
CACHE_DIR = Path(__file__).parent.parent / "cache" / "articles"

# Ensure cache directory exists
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def slugify(filename: str) -> str:
    """Convert filename to a clean article ID."""
    # Remove extension
    name = Path(filename).stem
    # Lowercase
    name = name.lower()
    # Replace spaces and special chars with hyphens
    name = re.sub(r'[^a-z0-9]+', '-', name)
    # Collapse multiple hyphens
    name = re.sub(r'-+', '-', name)
    # Strip leading/trailing hyphens
    name = name.strip('-')
    return name


def extract_pdf(pdf_path: Path) -> dict:
    """
    Send PDF to Datalab API and get HTML back.

    API is async - submit, then poll for results.
    """
    headers = {
        'X-API-Key': API_KEY,
    }

    # Submit the job
    with open(pdf_path, 'rb') as f:
        files = {'file': (pdf_path.name, f, 'application/pdf')}
        data = {
            'output_format': 'html',
            'mode': 'accurate',
        }

        print(f"  Uploading to Datalab API...")
        response = requests.post(API_URL, files=files, data=data, headers=headers)
        response.raise_for_status()
        result = response.json()

    if not result.get('success'):
        raise Exception(f"API error: {result.get('error')}")

    # Poll for completion
    check_url = result.get('request_check_url')
    request_id = result.get('request_id')
    print(f"  Processing... (request_id: {request_id})")

    max_attempts = 60  # 2 minutes max
    for attempt in range(max_attempts):
        time.sleep(2)

        check_response = requests.get(check_url, headers=headers)
        check_response.raise_for_status()
        status_result = check_response.json()

        status = status_result.get('status')

        if status == 'complete':
            print(f"  Complete!")
            return status_result
        elif status == 'failed':
            raise Exception(f"Extraction failed: {status_result.get('error')}")
        else:
            if attempt % 5 == 0:
                print(f"  Still processing... ({status})")

    raise Exception("Timeout waiting for extraction")


def embed_images_in_html(html: str, images: dict) -> str:
    """
    Replace image references with embedded base64 data URIs.

    Images dict is {filename: base64_data}.
    """
    if not images:
        return html

    for filename, base64_data in images.items():
        # Determine mime type from extension
        ext = Path(filename).suffix.lower()
        mime_types = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
        }
        mime = mime_types.get(ext, 'image/png')

        # Replace src="filename" with data URI
        data_uri = f"data:{mime};base64,{base64_data}"
        html = html.replace(f'src="{filename}"', f'src="{data_uri}"')
        html = html.replace(f"src='{filename}'", f"src='{data_uri}'")

    return html


def process_all_pdfs():
    """Process all PDFs in intake directory."""
    if not API_KEY:
        print("ERROR: Set DATALAB_API_KEY environment variable")
        print("  export DATALAB_API_KEY='your-key-here'")
        return

    pdf_files = sorted(INTAKE_DIR.glob("*.pdf"))
    print(f"Found {len(pdf_files)} PDFs in {INTAKE_DIR}")
    print()

    for i, pdf_path in enumerate(pdf_files, 1):
        article_id = slugify(pdf_path.name)
        output_path = CACHE_DIR / f"{article_id}.html"

        # Skip if already processed (and not empty)
        if output_path.exists() and output_path.stat().st_size > 0:
            print(f"[{i}/{len(pdf_files)}] SKIP (exists): {pdf_path.name}")
            continue

        print(f"[{i}/{len(pdf_files)}] Processing: {pdf_path.name}")
        print(f"  -> {article_id}.html")

        try:
            result = extract_pdf(pdf_path)

            html = result.get('html', '')
            images = result.get('images', {})

            if not html:
                print(f"  WARNING: No HTML returned")
                print(f"  Response keys: {result.keys()}")
                continue

            # Embed images if returned separately
            if images:
                print(f"  Embedding {len(images)} images...")
                html = embed_images_in_html(html, images)

            # Save HTML
            output_path.write_text(html, encoding='utf-8')

            # Report stats
            pages = result.get('page_count', '?')
            print(f"  Saved! {pages} pages, {len(html):,} chars")

        except Exception as e:
            print(f"  ERROR: {e}")

        print()

        # Small delay between requests to avoid rate limiting
        time.sleep(1)


if __name__ == "__main__":
    process_all_pdfs()
