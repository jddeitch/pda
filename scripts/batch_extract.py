#!/usr/bin/env python3
"""
Batch extract PDFs via Datalab API with structured JSON output.

Outputs structured JSON with block-level content including:
- PageHeader/PageFooter (for DOI, citation extraction)
- SectionHeader, Text, Table, Figure blocks
- Embedded images as base64 data URIs
"""

import requests
import os
import time
import re
import json
from pathlib import Path

API_KEY = os.environ.get('DATALAB_API_KEY', '-vPPAEwkoYbtFa9oa6cQRV1Gef8O1LaTSha-TZq5Yso')
API_URL = "https://www.datalab.to/api/v1/marker"

INTAKE_DIR = Path("/Users/jd/Projects/pda/intake/articles")
CACHE_DIR = Path("/Users/jd/Projects/pda/cache/articles")

# Rate limiting
DELAY_BETWEEN_SUBMISSIONS = 2  # seconds
DELAY_BETWEEN_POLLS = 3  # seconds
MAX_POLL_ATTEMPTS = 60  # 3 minutes max wait per file
BATCH_SIZE = 5  # Submit this many, then wait for all to complete


def slugify(filename: str) -> str:
    """Convert filename to a clean slug for output."""
    name = Path(filename).stem
    name = name.lower()
    name = re.sub(r'[^a-z0-9]+', '-', name)
    name = name.strip('-')
    name = re.sub(r'-+', '-', name)
    return name


def get_existing_files() -> set:
    """Get set of already-processed slugs (check for .json now)."""
    existing = set()
    for f in CACHE_DIR.glob("*.json"):
        existing.add(f.stem)
    return existing


def submit_pdf(pdf_path: Path) -> dict:
    """Submit a PDF for extraction with structured JSON output."""

    # Additional config for keeping page headers/footers
    additional_config = json.dumps({
        "keep_pageheader_in_output": True,
        "keep_pagefooter_in_output": True
    })

    with open(pdf_path, 'rb') as f:
        response = requests.post(
            API_URL,
            files={'file': (pdf_path.name, f, 'application/pdf')},
            data={
                'output_format': 'json',  # Get structured blocks
                'mode': 'accurate',
                'paginate': 'true',
                'skip_cache': 'true',
                'extras': 'extract_links',
                'additional_config': additional_config
            },
            headers={'X-API-Key': API_KEY}
        )

    if response.status_code == 429:
        print("  RATE LIMITED - waiting 60 seconds...")
        time.sleep(60)
        return submit_pdf(pdf_path)  # Retry

    return response.json()


def poll_and_save(request_id: str, output_path: Path) -> bool:
    """Poll for completion and save structured JSON with embedded images."""
    for attempt in range(MAX_POLL_ATTEMPTS):
        response = requests.get(
            f"{API_URL}/{request_id}",
            headers={'X-API-Key': API_KEY}
        )
        data = response.json()
        status = data.get('status')

        if status == 'complete':
            # New API structure (2025): data['json']['children'] contains Pages
            # Each Page has 'children' with the actual blocks
            # Old structure: data['chunks']['blocks'] or data['blocks']
            blocks = []
            images = data.get('images') or {}

            # Try new structure first: json -> children (Pages) -> children (blocks)
            json_data = data.get('json')
            if json_data and isinstance(json_data, dict):
                pages = json_data.get('children', [])
                page_num = 0
                for page in pages:
                    if page.get('block_type') == 'Page':
                        page_children = page.get('children', [])
                        for block in page_children:
                            block['page'] = page_num
                            blocks.append(block)
                        page_num += 1

            # Fall back to old structure if new structure didn't work
            if not blocks:
                chunks = data.get('chunks') or {}
                blocks = chunks.get('blocks') if isinstance(chunks, dict) else None
                if not blocks:
                    blocks = data.get('blocks', [])

            if not blocks:
                print(f"  WARNING: No blocks returned")
                return False

            # Embed images as base64 data URIs in block HTML
            for block in blocks:
                html = block.get('html', '')
                for filename, b64_data in images.items():
                    if filename in html:
                        ext = filename.split('.')[-1].lower()
                        mime = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png'}.get(ext, 'image/jpeg')
                        data_uri = f'data:{mime};base64,{b64_data}'
                        html = html.replace(f'src="{filename}"', f'src="{data_uri}"')
                        block['html'] = html

            # Save the full structured response
            page_count = data.get('page_count') or (max((b.get('page', 0) for b in blocks), default=0) + 1)
            output_data = {
                'status': 'complete',
                'blocks': blocks,
                'images_count': len(images),
                'page_count': page_count
            }

            output_path.write_text(json.dumps(output_data, indent=2, ensure_ascii=False))
            print(f"  COMPLETE - {len(blocks)} blocks, {len(images)} images, {page_count} pages")
            return True

        elif status == 'error':
            print(f"  ERROR: {data.get('error', 'Unknown error')}")
            return False
        else:
            if attempt % 10 == 0:
                print(f"  {status}... (attempt {attempt+1})")
            time.sleep(DELAY_BETWEEN_POLLS)

    print(f"  TIMEOUT after {MAX_POLL_ATTEMPTS} attempts")
    return False


def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    existing = get_existing_files()
    print(f"Already processed: {len(existing)} files")

    # Get all PDFs
    pdfs = sorted(INTAKE_DIR.glob("*.pdf"))
    print(f"Total PDFs in intake: {len(pdfs)}")

    # Filter to unprocessed
    to_process = []
    for pdf in pdfs:
        slug = slugify(pdf.name)
        if slug not in existing:
            to_process.append((pdf, slug))

    print(f"Remaining to process: {len(to_process)}")
    print()

    if not to_process:
        print("Nothing to do!")
        return

    # Process in batches
    for batch_start in range(0, len(to_process), BATCH_SIZE):
        batch = to_process[batch_start:batch_start + BATCH_SIZE]
        print(f"=== Batch {batch_start // BATCH_SIZE + 1} ({len(batch)} files) ===")

        # Submit all in batch
        submissions = []
        for pdf_path, slug in batch:
            print(f"Submitting: {pdf_path.name[:60]}...")
            result = submit_pdf(pdf_path)
            request_id = result.get('request_id')
            if request_id:
                submissions.append((request_id, slug, pdf_path.name))
                print(f"  Request ID: {request_id}")
            else:
                print(f"  FAILED: {result}")
            time.sleep(DELAY_BETWEEN_SUBMISSIONS)

        print()

        # Poll all submissions
        for request_id, slug, filename in submissions:
            output_path = CACHE_DIR / f"{slug}.json"
            print(f"Waiting for: {filename[:50]}...")
            poll_and_save(request_id, output_path)

        print()

        # Pause between batches
        if batch_start + BATCH_SIZE < len(to_process):
            print("Pausing 10 seconds between batches...")
            time.sleep(10)


if __name__ == "__main__":
    main()
