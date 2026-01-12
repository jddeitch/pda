#!/usr/bin/env python3
"""
Parse extracted HTML articles into structured components:
- title
- authors
- journal/citation
- abstract
- body_html (main content)
- acknowledgements
- references
- figures (with captions)
- tables (with captions)
"""

import re
import json
import sys
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString
import yaml


def load_section_headings() -> dict[str, list[str]]:
    """Load multilingual section heading patterns from YAML."""
    yaml_path = Path(__file__).parent.parent / 'data' / 'section_headings.yaml'
    if not yaml_path.exists():
        # Fallback to basic English patterns
        return {
            'abstract': ['abstract', 'summary'],
            'references': ['reference', 'bibliography'],
            'acknowledgements': ['acknowledgement', 'acknowledgment'],
            'keywords': ['keyword'],
        }

    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    # Flatten all language patterns into a single list per section
    result = {}
    for section, langs in data.items():
        patterns = []
        for lang, terms in langs.items():
            patterns.extend(terms)
        result[section] = patterns

    return result


SECTION_HEADINGS = load_section_headings()


def matches_section(text: str, section: str) -> bool:
    """Check if text matches any pattern for the given section type."""
    text_lower = text.lower()
    for pattern in SECTION_HEADINGS.get(section, []):
        if pattern in text_lower:
            return True
    return False


def extract_doi(text: str) -> str | None:
    """Extract DOI from text if present."""
    # DOI pattern: 10.XXXX/anything (until whitespace, comma, or end)
    doi_match = re.search(r'10\.\d{4,}/[^\s,<>]+', text)
    if doi_match:
        # Clean trailing punctuation
        doi = doi_match.group(0).rstrip('.,;:)"\'')
        return doi
    return None


def is_cruft(text: str, element=None) -> bool:
    """Detect metadata cruft that should be stripped from body."""
    text_lower = text.lower().strip()

    cruft_patterns = [
        # Copyright
        r'©\s*\d{4}',
        r'all rights reserved',
        r'tous droits réservés',
        r'creative commons',
        # Keywords (as standalone block)
        r'^keywords?\s',
        r'^mots[\s-]?cl[ée]s?\s*:',
        # Correspondence / author contact
        r'correspond\w*\s*(to|author)',
        r'auteur correspondant',
        r'^✉',  # Email symbol
        # Dates (at start of text)
        r'^accepted\s+\d',
        r'^received\s+\d',
        r'^published\s+(online\s+)?\d',
        # Journal cruft
        r'^doi[\s:]+10\.',
        r'downloaded from',
        r'see end of article',
        r'^electronic supplementary material',
        # Author email blocks
        r'^[a-z]+\.[a-z]+@[a-z]+\.',
    ]

    for pattern in cruft_patterns:
        if re.search(pattern, text_lower):
            return True

    # Affiliation blocks: start with superscript number followed by institution
    # Pattern: "1 Centre for...", "2 Faculty of...", "3 Department of..."
    if re.match(r'^\d+\s+(centre|center|faculty|department|division|school|institute|university|hospital|mrc\s)', text_lower):
        return True

    # Very short text that's likely a label (but not section headings)
    if len(text) < 30 and text.endswith(':') and not text[0].isdigit():
        return True

    return False


def find_body_start_heading(elements, start_idx: int) -> int:
    """Find the index of the first real content heading (Introduction, Background, etc.)."""
    body_start_patterns = [
        r'^1\.?\s',  # Numbered section "1." or "1 "
        r'^introduction$',
        r'^background$',
        r'^overview$',
        r'^methods?$',
        r'^literature review$',
        r'^contexte?$',  # French
        r'^préambule$',
        r'^méthodes?$',
    ]

    for i in range(start_idx, len(elements)):
        el = elements[i]
        if el.name in ['h1', 'h2', 'h3']:
            text = el.get_text(strip=True).lower()
            for pattern in body_start_patterns:
                if re.match(pattern, text):
                    return i

    return start_idx  # Fallback to current position


def parse_article(html_path: Path) -> dict:
    """Parse an article HTML file into structured components."""

    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')
    body = soup.find('body')

    if not body:
        return {'error': 'No body element found'}

    result = {
        'source_file': html_path.name,
        'title': None,
        'article_type': None,  # e.g., "REVIEW", "RESEARCH ARTICLE"
        'authors': None,
        'citation': None,
        'doi': None,  # Extracted from header/footer cruft
        'abstract': None,
        'abstract_fr': None,  # For bilingual papers
        'abstract_en': None,
        'keywords': None,
        'keywords_fr': None,
        'body_html': None,
        'acknowledgements': None,
        'references': [],
        'figures': [],
        'tables': [],
        'stripped_cruft': [],  # Track what we removed
        'warnings': [],  # Potential issues for human review
    }

    elements = list(body.children)
    elements = [e for e in elements if not isinstance(e, NavigableString) or e.strip()]

    idx = 0

    # --- Extract title (first h1, possibly preceded by article type) ---
    article_type_labels = [
        'REVIEW', 'RESEARCH ARTICLE', 'ORIGINAL ARTICLE', 'COMMENTARY',
        'CASE REPORT', 'LETTER', 'EDITORIAL', 'SHORT COMMUNICATION',
        # French
        'REVUE DE LITTÉRATURE', 'REVUE DE LITTERATURE', 'ARTICLE ORIGINAL',
        'ARTICLE DE RECHERCHE', 'CAS CLINIQUE',
    ]

    while idx < len(elements):
        el = elements[idx]
        if isinstance(el, NavigableString):
            idx += 1
            continue
        if el.name == 'h1':
            text = el.get_text(strip=True)
            # Check if this is an article type label
            if text.upper() in article_type_labels:
                result['article_type'] = text
                idx += 1
                # Next h1 should be the actual title
                if idx < len(elements) and elements[idx].name == 'h1':
                    result['title'] = elements[idx].get_text(strip=True)
                    idx += 1
            else:
                result['title'] = text
                idx += 1
            break
        idx += 1

    # --- Extract authors (usually first <p> after title) ---
    if idx < len(elements):
        el = elements[idx]
        if el.name == 'p':
            text = el.get_text(strip=True)
            # Heuristic: authors line is short, has commas, no periods in middle
            if len(text) < 200 and ',' in text and not re.search(r'\.\s+[A-Z]', text):
                result['authors'] = text
                idx += 1

    # --- Extract citation (journal, year, pages) ---
    if idx < len(elements):
        el = elements[idx]
        if el.name == 'p':
            text = el.get_text(strip=True)
            # Heuristic: citation has year pattern and often semicolon
            if re.search(r'\d{4}', text) and (';' in text or 'vol' in text.lower() or re.search(r'\d+:\d+', text)):
                result['citation'] = text
                # Extract DOI from citation if present
                if not result['doi']:
                    result['doi'] = extract_doi(text)
                idx += 1

    # --- Extract abstract ---
    # Look for "Abstract" heading or first substantial paragraph before main sections
    abstract_parts = []
    while idx < len(elements):
        el = elements[idx]
        if isinstance(el, NavigableString):
            idx += 1
            continue

        # Check for explicit Abstract heading
        if el.name in ['h2', 'h3', 'strong', 'b']:
            text = el.get_text(strip=True).lower()
            if 'abstract' in text:
                idx += 1
                # Collect paragraphs until next heading
                while idx < len(elements):
                    next_el = elements[idx]
                    if next_el.name in ['h1', 'h2', 'h3']:
                        break
                    if next_el.name == 'p':
                        abstract_parts.append(next_el.get_text(strip=True))
                    idx += 1
                break

        # Check if this paragraph looks like an abstract (before any h2)
        if el.name == 'p':
            text = el.get_text(strip=True)
            # Skip metadata-looking paragraphs
            if any(skip in text.lower() for skip in ['correspondence to:', 'accepted', 'see end of article', 'additional information']):
                idx += 1
                continue
            # Skip image descriptions
            if el.find_parent('div', class_='img-description'):
                idx += 1
                continue
            # If we hit a section heading, stop looking for abstract
            if len(text) < 50 and text.isupper():
                break
            # Long paragraph before first h2 is likely abstract
            if len(text) > 200:
                abstract_parts.append(text)
                idx += 1
                # Check if next para continues abstract
                while idx < len(elements) and elements[idx].name == 'p':
                    next_text = elements[idx].get_text(strip=True)
                    if len(next_text) > 100 and not any(skip in next_text.lower() for skip in ['correspondence', 'accepted', 'see end']):
                        abstract_parts.append(next_text)
                        idx += 1
                    else:
                        break
                break

        # If we hit an h2, no explicit abstract found
        if el.name == 'h2':
            break

        idx += 1

    if abstract_parts:
        result['abstract'] = ' '.join(abstract_parts)

    # --- Find where body actually starts (first content heading) ---
    body_start_idx = find_body_start_heading(elements, idx)

    # --- Find where body ends ---
    body_end_idx = len(elements)
    references_start_idx = None
    acknowledgements_start_idx = None

    for i in range(body_start_idx, len(elements)):
        el = elements[i]
        if el.name in ['h1', 'h2', 'h3']:
            text = el.get_text(strip=True)
            if matches_section(text, 'references'):
                if references_start_idx is None:
                    references_start_idx = i
                    if acknowledgements_start_idx is None:
                        body_end_idx = i
            elif matches_section(text, 'acknowledgements'):
                acknowledgements_start_idx = i
                if references_start_idx is None:  # Ack before refs
                    body_end_idx = i

    # --- Extract references ---
    if references_start_idx is not None:
        ref_idx = references_start_idx + 1
        while ref_idx < len(elements):
            el = elements[ref_idx]
            if el.name in ['h1', 'h2', 'h3']:
                text = el.get_text(strip=True)
                if not matches_section(text, 'references'):
                    break
            if el.name == 'ol':
                for li in el.find_all('li'):
                    result['references'].append(li.get_text(strip=True))
            elif el.name == 'li':
                result['references'].append(el.get_text(strip=True))
            elif el.name == 'p':
                text = el.get_text(strip=True)
                # Numbered reference
                if re.match(r'^\d+\.?\s', text):
                    result['references'].append(text)
            ref_idx += 1

    # --- Extract acknowledgements ---
    if acknowledgements_start_idx is not None:
        ack_parts = []
        ack_idx = acknowledgements_start_idx + 1
        end_idx = references_start_idx if references_start_idx and references_start_idx > acknowledgements_start_idx else len(elements)
        while ack_idx < end_idx:
            el = elements[ack_idx]
            if el.name in ['h1', 'h2', 'h3']:
                break
            if el.name == 'p':
                ack_parts.append(el.get_text(strip=True))
            ack_idx += 1
        if ack_parts:
            result['acknowledgements'] = ' '.join(ack_parts)

    # --- Extract figures and tables from body ---
    for i in range(body_start_idx, body_end_idx):
        el = elements[i]

        # Figures
        if el.name == 'img':
            fig = {
                'alt': el.get('alt', ''),
                'src': el.get('src', '')[:100] + '...' if el.get('src', '').startswith('data:') else el.get('src', ''),
                'caption': None
            }
            # Look for caption in adjacent div
            next_sib = el.find_next_sibling()
            if next_sib and next_sib.name == 'div' and 'img-description' in next_sib.get('class', []):
                fig['caption'] = next_sib.get_text(strip=True)
            result['figures'].append(fig)

        # Tables
        if el.name == 'table':
            tab = {
                'caption': None,
                'preview': str(el)[:200] + '...'
            }
            # Look for caption - might be in previous element or thead
            prev = el.find_previous_sibling()
            if prev and prev.name == 'p':
                text = prev.get_text(strip=True)
                if text.lower().startswith('table'):
                    tab['caption'] = text
            result['tables'].append(tab)

    # --- Build body HTML, filtering out cruft ---
    body_elements = elements[body_start_idx:body_end_idx]
    filtered_body = []

    for el in body_elements:
        if isinstance(el, NavigableString):
            continue

        # Get text content for cruft detection
        text = el.get_text(strip=True) if hasattr(el, 'get_text') else str(el)

        # Skip cruft (but extract DOI first if present)
        if is_cruft(text):
            # Try to extract DOI from cruft before discarding
            if not result['doi']:
                doi = extract_doi(text)
                if doi:
                    result['doi'] = doi
            result['stripped_cruft'].append(text[:100])
            continue

        # Skip standalone images before first heading (usually journal logos)
        if el.name == 'img' and not filtered_body:
            continue
        if el.name == 'div' and 'img-description' in el.get('class', []) and not filtered_body:
            continue

        # Skip abbreviations blocks
        if el.name in ['p', 'div'] and text.lower().startswith('abbreviation'):
            result['stripped_cruft'].append(text[:100])
            continue

        filtered_body.append(el)

    # --- Join paragraphs split by page breaks ---
    # Heuristic: if a <p> doesn't end with terminal punctuation and next <p> starts lowercase,
    # they were likely split by a page break
    joined_body = []
    i = 0
    while i < len(filtered_body):
        el = filtered_body[i]

        if el.name == 'p' and i + 1 < len(filtered_body):
            next_el = filtered_body[i + 1]
            if next_el.name == 'p':
                current_text = el.get_text(strip=True)
                next_text = next_el.get_text(strip=True)

                # Check if current para doesn't end with terminal punctuation
                # and next para starts with lowercase
                if (current_text and next_text and
                    not current_text[-1] in '.!?:;"' and
                    next_text[0].islower()):
                    # Join them - append contents of next_el into el
                    # Preserve HTML structure of both elements
                    from bs4 import NavigableString as NS
                    # Add a space and then all children from next_el
                    el.append(NS(' '))
                    for child in list(next_el.children):
                        el.append(child.extract())
                    joined_body.append(el)
                    result['stripped_cruft'].append(f"[JOINED] ...{current_text[-30:]} + {next_text[:30]}...")
                    i += 2  # Skip both elements
                    continue

        joined_body.append(el)
        i += 1

    result['body_html'] = ''.join(str(el) for el in joined_body)

    # --- Detect potential orphan paragraphs (start with lowercase) ---
    for el in joined_body:
        if el.name == 'p':
            text = el.get_text(strip=True)
            if text and text[0].islower():
                # Likely an orphan from a page/column break
                preview = text[:60] + '...' if len(text) > 60 else text
                result['warnings'].append(f"[ORPHAN?] Paragraph starts lowercase: \"{preview}\"")

    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python parse_article_structure.py <html_file>")
        sys.exit(1)

    html_path = Path(sys.argv[1])
    result = parse_article(html_path)

    # Print summary
    print(f"=== {result['source_file']} ===\n")
    print(f"Article Type: {result['article_type']}")
    print(f"Title: {result['title']}")
    print(f"Authors: {result['authors']}")
    print(f"Citation: {result['citation']}")
    print(f"\nAbstract ({len(result['abstract'] or '')} chars):")
    if result['abstract']:
        print(f"  {result['abstract'][:300]}...")
    print(f"\nBody HTML: {len(result['body_html'] or '')} chars")
    print(f"Acknowledgements: {'Yes' if result['acknowledgements'] else 'No'}")
    print(f"References: {len(result['references'])}")
    print(f"Figures: {len(result['figures'])}")
    print(f"Tables: {len(result['tables'])}")

    if result['stripped_cruft']:
        print(f"\nStripped cruft ({len(result['stripped_cruft'])} items):")
        for cruft in result['stripped_cruft'][:5]:
            print(f"  - {cruft[:80]}...")

    if result['warnings']:
        print(f"\nWarnings ({len(result['warnings'])} items):")
        for warning in result['warnings']:
            print(f"  - {warning}")

    # Save full result as JSON
    output_path = html_path.with_suffix('.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        # Don't include full body_html in JSON for readability
        result_for_json = {**result, 'body_html': f"[{len(result['body_html'] or '')} chars]"}
        json.dump(result_for_json, f, indent=2, ensure_ascii=False)
    print(f"\nFull result saved to: {output_path}")


if __name__ == '__main__':
    main()
