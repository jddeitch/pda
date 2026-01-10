#!/usr/bin/env python3
"""
Parse the PDA Society Research Overviews HTML file into YAML format.
Uses BeautifulSoup for more reliable parsing.
"""

import re
from html import unescape
import yaml
from bs4 import BeautifulSoup
import os


def clean_text(text):
    """Clean up HTML entities and extra whitespace."""
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def parse_html_file(filepath):
    """Parse the HTML file and extract research resources."""
    with open(filepath, 'r', encoding='utf-8') as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, 'html.parser')

    resources = []

    # Find all research resource divs
    for div in soup.find_all('div', class_='crp-resource'):
        resource = {
            "id": div.get('id', ''),
            "title_en": "",
            "title_fr": "",
            "year": "",
            "keywords": [],
            "authors": "",
            "summary_en": "",
            "summary_fr": "",
            "url": "",
            "open_access": None,
            "doi": "",
            "category": "",
            "translation_status": "not_started"
        }

        # Extract title
        h2 = div.find('h2')
        if h2:
            title = clean_text(h2.get_text())
            if title.startswith("Title:"):
                title = title[6:].strip()
            resource["title_en"] = title

        # Extract URL from crp-link
        link = div.find('a', class_='crp-link')
        if link:
            resource["url"] = link.get('href', '')

        # Parse the text content for fields
        text_content = div.get_text()

        # Year
        year_match = re.search(r'Year Published:\s*(\d{4})', text_content)
        if year_match:
            resource["year"] = year_match.group(1)

        # Keywords
        keywords_match = re.search(r'Keywords:\s*([^\n]+)', text_content)
        if keywords_match:
            keywords_text = keywords_match.group(1).strip()
            # Stop at "Authors:" if present
            if "Authors:" in keywords_text:
                keywords_text = keywords_text.split("Authors:")[0].strip()
            resource["keywords"] = [k.strip() for k in keywords_text.split(",") if k.strip()]

        # Authors
        authors_match = re.search(r'Authors:\s*([^\n]+)', text_content)
        if authors_match:
            authors_text = authors_match.group(1).strip()
            # Stop at "Summary:" if present
            if "Summary:" in authors_text:
                authors_text = authors_text.split("Summary:")[0].strip()
            resource["authors"] = clean_text(authors_text)

        # Summary - get all paragraphs after "Summary:"
        summary_parts = []
        found_summary = False
        for p in div.find_all('p'):
            p_text = clean_text(p.get_text())
            if 'Summary:' in p_text:
                found_summary = True
                # Get text after "Summary:" if on same line
                after_summary = p_text.split('Summary:')[-1].strip()
                if after_summary:
                    summary_parts.append(after_summary)
            elif found_summary and p_text and not p_text.startswith('Read here'):
                # Skip the "Read here" link text and field labels
                if not any(label in p_text for label in ['Year Published:', 'Keywords:', 'Authors:']):
                    summary_parts.append(p_text)

        resource["summary_en"] = "\n\n".join(summary_parts)

        # Check if open access based on URL or link text
        if link:
            link_text = clean_text(link.get_text())
            if "payment" in link_text.lower():
                resource["open_access"] = False
            elif resource["url"]:
                resource["open_access"] = True  # Assume open if link exists without payment mention

        # Extract DOI from URL if present
        url = resource.get("url", "")
        doi_match = re.search(r'doi\.org/(10\.\d+/[^\s]+)', url)
        if doi_match:
            resource["doi"] = doi_match.group(1)

        resources.append(resource)

    return resources


def main():
    html_file = "/Users/jd/Projects/pda/external/research_overviews_extracted.html"
    output_file = "/Users/jd/Projects/pda/data/pda_research.yaml"

    print(f"Parsing {html_file}...")
    resources = parse_html_file(html_file)

    print(f"Found {len(resources)} resources")

    # Create output directory if needed
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    # Write to YAML with nice formatting
    with open(output_file, 'w', encoding='utf-8') as f:
        yaml.dump(
            {"resources": resources},
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120
        )

    print(f"Written to {output_file}")

    # Print summary
    print("\nFirst 5 resources:")
    for r in resources[:5]:
        print(f"\n- {r['title_en']} ({r['year']})")
        print(f"  Authors: {r['authors'][:60]}..." if len(r['authors']) > 60 else f"  Authors: {r['authors']}")


if __name__ == "__main__":
    main()
