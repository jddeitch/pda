#!/usr/bin/env python3
"""
Parse Datalab structured JSON blocks into article components.

Works with the new JSON output format from batch_extract.py which includes:
- PageHeader/PageFooter blocks (for DOI, citation extraction)
- SectionHeader, Text, Table, Figure blocks with page numbers
- Embedded images as base64 data URIs

Key improvements over HTML parsing:
- DOI extracted directly from PageFooter blocks
- Citation/authors from PageHeader blocks
- No need for heuristic section detection - Datalab provides block_type
"""

import re
import json
import sys
from pathlib import Path
from bs4 import BeautifulSoup
import yaml


def load_section_headings() -> dict[str, list[str]]:
    """Load multilingual section heading patterns from YAML."""
    yaml_path = Path(__file__).parent.parent / 'data' / 'section_headings.yaml'
    if not yaml_path.exists():
        return {
            'abstract': ['abstract', 'summary', 'résumé'],
            'references': ['reference', 'bibliography', 'références', 'bibliographie'],
            'acknowledgements': ['acknowledgement', 'acknowledgment', 'remerciements'],
            'keywords': ['keyword', 'mots-clés', 'mots clés'],
        }

    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

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
    text_lower = text.lower().strip()
    for pattern in SECTION_HEADINGS.get(section, []):
        if pattern in text_lower:
            return True
    return False


def extract_doi(text: str) -> str | None:
    """Extract DOI from text if present."""
    doi_match = re.search(r'10\.\d{4,}/[^\s,<>\"]+', text)
    if doi_match:
        doi = doi_match.group(0).rstrip('.,;:)"\'')
        return doi
    return None


def extract_text(html: str) -> str:
    """Extract plain text from HTML."""
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text(strip=True)


def extract_year(text: str) -> str | None:
    """Extract 4-digit year from text."""
    match = re.search(r'\b(19|20)\d{2}\b', text)
    return match.group(0) if match else None


def is_article_type_label(text: str) -> bool:
    """Check if text is an article type label."""
    labels = [
        'REVIEW', 'RESEARCH ARTICLE', 'ORIGINAL ARTICLE', 'COMMENTARY',
        'CASE REPORT', 'LETTER', 'EDITORIAL', 'SHORT COMMUNICATION',
        'REVUE DE LITTÉRATURE', 'REVUE DE LITTERATURE', 'ARTICLE ORIGINAL',
        'ARTICLE DE RECHERCHE', 'CAS CLINIQUE', 'LITERATURE REVIEW',
    ]
    return text.upper().strip() in labels


def is_complete_sentence(text: str) -> bool:
    """Check if text appears to be a complete sentence (starts uppercase, ends with punctuation)."""
    text = text.strip()
    if not text:
        return False
    # Starts with uppercase or number, ends with sentence-ending punctuation
    starts_properly = text[0].isupper() or text[0].isdigit()
    ends_properly = text[-1] in '.!?'
    return starts_properly and ends_properly


def is_sentence_continuation(text: str) -> bool:
    """Check if text appears to be a continuation (starts with lowercase)."""
    text = text.strip()
    if not text:
        return False
    # Starts with lowercase letter
    return text[0].islower()


def is_incomplete_sentence(text: str) -> bool:
    """Check if text appears to be incomplete (doesn't end with sentence punctuation)."""
    text = text.strip()
    if not text:
        return False
    return text[-1] not in '.!?:;'


def join_split_sentences(blocks: list[dict]) -> list[dict]:
    """
    Join sentences that were split across page/column breaks.

    Strategy:
    1. If current block is incomplete (doesn't end with .!?) AND the next text/caption block
       is immediately adjacent or across a page boundary, join them regardless of what the
       next block starts with.
    2. If we crossed a page boundary AND the next text starts lowercase, join them even if
       the current block looks complete (handles abbreviations like "e.g.", "i.e.").

    Returns a new list of blocks with split sentences joined.
    """
    result = []
    i = 0
    consumed = set()  # Track which blocks have been consumed as continuations

    while i < len(blocks):
        if i in consumed:
            i += 1
            continue

        block = blocks[i]
        bt = block.get('block_type', '')

        # Process both Text and Caption blocks for potential splits
        if bt in ['Text', 'Caption']:
            text = extract_text(block.get('html', ''))
            current_incomplete = is_incomplete_sentence(text)

            # Look ahead for a continuation
            j = i + 1
            continuation_idx = None
            crossed_page_boundary = False

            while j < len(blocks):
                next_block = blocks[j]
                next_bt = next_block.get('block_type', '')

                # Track if we crossed a page boundary
                if next_bt in ['PageFooter', 'PageHeader']:
                    crossed_page_boundary = True
                    j += 1
                    continue

                # Skip over Tables (text can continue after a table)
                if next_bt == 'Table':
                    j += 1
                    continue

                # Figures and Pictures break the flow - their captions belong to them, not previous text
                if next_bt in ['Figure', 'Picture']:
                    break

                if next_bt in ['Text', 'Caption']:
                    next_text = extract_text(next_block.get('html', ''))

                    # Skip captions that are clearly for tables/figures (not continuations)
                    if next_bt == 'Caption' and re.match(r'^(Table|Fig\.?|Figure)\s*\d', next_text, re.IGNORECASE):
                        j += 1
                        continue

                    # If next block starts lowercase, it's a continuation
                    if is_sentence_continuation(next_text):
                        continuation_idx = j
                        break

                    # If current block is incomplete and next is on same page (column break),
                    # join regardless of case
                    if current_incomplete and not crossed_page_boundary and block.get('page') == next_block.get('page'):
                        continuation_idx = j
                        break

                    # If we crossed a page boundary but next doesn't start lowercase,
                    # keep looking - there might be intervening complete sentences (like table footnotes)
                    if crossed_page_boundary:
                        j += 1
                        continue

                    # Otherwise, this text block is complete and next is a new paragraph
                    break

                # SectionHeader or other block type ends the search
                break

            # Join if we found a continuation
            if continuation_idx is not None:
                continuation_text = extract_text(blocks[continuation_idx].get('html', ''))
                joined_text = text + ' ' + continuation_text
                new_block = block.copy()
                new_block['html'] = f'<p>{joined_text}</p>'
                new_block['_joined'] = True  # Mark as joined for debugging
                new_block['_joined_from'] = continuation_idx
                result.append(new_block)
                consumed.add(continuation_idx)  # Don't output continuation separately
                i += 1
                continue

        result.append(block)
        i += 1

    return result


def parse_blocks(json_path: Path) -> dict:
    """Parse Datalab JSON blocks into structured article components."""

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    blocks = data.get('blocks', [])

    # Join sentences that were split across page breaks
    blocks = join_split_sentences(blocks)

    result = {
        'source_file': json_path.name,
        'title': None,
        'article_type': None,
        'authors': None,
        'citation': None,
        'doi': None,
        'year': None,
        'abstract': None,
        'abstract_fr': None,
        'abstract_en': None,
        'keywords': None,
        'keywords_fr': None,
        'body_html': None,
        'acknowledgements': None,
        'references': [],
        'figures': [],
        'tables': [],
        'page_headers': [],  # All page headers for debugging
        'page_footers': [],  # All page footers for debugging
        'warnings': [],
    }

    # Separate blocks by type
    page_headers = [b for b in blocks if b.get('block_type') == 'PageHeader']
    page_footers = [b for b in blocks if b.get('block_type') == 'PageFooter']
    section_headers = [b for b in blocks if b.get('block_type') == 'SectionHeader']
    text_blocks = [b for b in blocks if b.get('block_type') == 'Text']
    footnotes = [b for b in blocks if b.get('block_type') == 'Footnote']
    tables = [b for b in blocks if b.get('block_type') == 'Table']
    figures = [b for b in blocks if b.get('block_type') == 'Figure']

    # Store raw headers/footers for debugging
    for h in page_headers:
        result['page_headers'].append({
            'page': h.get('page'),
            'text': extract_text(h.get('html', ''))
        })
    for f in page_footers:
        result['page_footers'].append({
            'page': f.get('page'),
            'text': extract_text(f.get('html', ''))
        })

    # --- Extract DOI from page footers or headers (usually page 0) ---
    for footer in page_footers:
        html = footer.get('html', '')
        doi = extract_doi(html)
        if doi:
            result['doi'] = doi
            break

    # If no DOI in footers, check headers (some journals put DOI there)
    if not result['doi']:
        for header in page_headers:
            html = header.get('html', '')
            doi = extract_doi(html)
            if doi:
                result['doi'] = doi
                break

    # Fallback: check all text blocks on page 0-1 for DOI (ResearchGate, preprints)
    if not result['doi']:
        for block in blocks:
            if block.get('page', 0) > 1:
                break  # Only check first 2 pages
            if block.get('block_type') == 'Text':
                doi = extract_doi(block.get('html', ''))
                if doi:
                    result['doi'] = doi
                    break

    # --- Extract citation from page headers ---
    # Look for journal citation pattern: "Journal Name Vol (Year) Pages"
    for header in page_headers:
        text = extract_text(header.get('html', ''))
        # Skip page numbers, publisher names
        if re.match(r'^\d+$', text):  # Just a page number
            continue
        if text.upper() in ['ELSEVIER', 'CROSSMARK', 'SPRINGER', 'WILEY']:
            continue
        # Look for citation pattern with year and page range
        if re.search(r'\d{4}.*\d+[-–]\d+', text) or re.search(r'\(\d{4}\)', text):
            result['citation'] = text
            result['year'] = extract_year(text)
            break

    # Fallback: extract year from early text blocks if not found in citation
    if not result['year']:
        for block in blocks:
            if block.get('page', 0) > 1:
                break  # Only check first 2 pages
            if block.get('block_type') == 'Text':
                text = extract_text(block.get('html', ''))
                # Look for publication year patterns (not in-text citations like "Wing, 1991")
                # Match: © 2013, (2015), · July 2015, published 2014
                year_patterns = [
                    r'©.*?(20\d{2})',  # Copyright year (no word boundary - "2013Reprints")
                    r'·\s*\w+\s*(20\d{2})',  # · July 2015 style
                    r'published[:\s]+.*?(20\d{2})',  # Published date
                    r'\((20\d{2})\)\s*\d+[-–]\d+',  # (2020) 50:386-401 style
                ]
                for pattern in year_patterns:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        result['year'] = match.group(1)
                        break
                if result['year']:
                    break

    # --- Extract title and article type from first section headers ---
    for i, sh in enumerate(section_headers):
        if sh.get('page', 0) > 0:
            break  # Only look at page 0
        text = extract_text(sh.get('html', ''))

        if is_article_type_label(text):
            result['article_type'] = text
        elif not result['title'] and len(text) > 20:
            # First substantial heading is likely the title
            result['title'] = text

    # --- Extract authors from text blocks on page 0 ---
    for block in text_blocks:
        if block.get('page', 0) > 0:
            break
        text = extract_text(block.get('html', ''))
        # Authors usually have superscript markers and commas
        if re.search(r'[A-Z]\.\s*[A-Z][a-z]+.*,', text) and len(text) < 300:
            # Strip superscript markers
            authors = re.sub(r'<sup>.*?</sup>', '', block.get('html', ''))
            authors = extract_text(authors)
            # Clean up multiple commas/spaces
            authors = re.sub(r',\s*,', ',', authors)
            authors = re.sub(r'\s+', ' ', authors).strip()
            if not result['authors']:
                result['authors'] = authors
            break

    # --- Find abstract section ---
    in_abstract = False
    abstract_parts = []
    abstract_lang = None

    for block in blocks:
        bt = block.get('block_type', '')
        html = block.get('html', '')
        text = extract_text(html)

        if bt == 'SectionHeader':
            text_lower = text.lower().strip()
            if matches_section(text, 'abstract'):
                # Save previous abstract if we were already in one (bilingual papers)
                if in_abstract and abstract_parts:
                    abstract_text = ' '.join(abstract_parts)
                    if abstract_lang == 'fr':
                        result['abstract_fr'] = abstract_text
                    elif abstract_lang == 'en':
                        result['abstract_en'] = abstract_text
                    else:
                        result['abstract'] = abstract_text
                    abstract_parts = []

                in_abstract = True
                # Detect language - check French first since 'résumé' is more specific
                if 'résumé' in text_lower or 'resume' in text_lower:
                    abstract_lang = 'fr'
                elif 'abstract' in text_lower or 'summary' in text_lower:
                    abstract_lang = 'en'
                else:
                    abstract_lang = None  # Unknown, use generic
                continue
            elif in_abstract:
                # Hit a non-abstract section, end abstract
                in_abstract = False
                if abstract_parts:
                    abstract_text = ' '.join(abstract_parts)
                    if abstract_lang == 'fr':
                        result['abstract_fr'] = abstract_text
                    elif abstract_lang == 'en':
                        result['abstract_en'] = abstract_text
                    else:
                        result['abstract'] = abstract_text
                    abstract_parts = []

        elif bt == 'Text' and in_abstract:
            abstract_parts.append(text)

    # Handle case where abstract runs to end
    if abstract_parts:
        abstract_text = ' '.join(abstract_parts)
        if abstract_lang == 'fr':
            result['abstract_fr'] = abstract_text
        elif abstract_lang == 'en':
            result['abstract_en'] = abstract_text
        else:
            result['abstract'] = abstract_text

    # Set main abstract if we only have language-specific ones
    if not result['abstract']:
        result['abstract'] = result['abstract_en'] or result['abstract_fr']

    # Fallback: Look for inline abstract in Text blocks (e.g., ResearchGate format)
    # These start with "Abstract" followed by the content
    if not result['abstract']:
        for block in blocks:
            if block.get('block_type') == 'Text':
                text = extract_text(block.get('html', ''))
                if text.lower().startswith('abstract'):
                    # Extract everything after "Abstract"
                    abstract_text = re.sub(r'^abstract\s*', '', text, flags=re.IGNORECASE)
                    if len(abstract_text) > 100:  # Sanity check - real abstract should be substantial
                        result['abstract'] = abstract_text
                        result['abstract_en'] = abstract_text
                        break

    # --- Extract keywords ---
    for block in blocks:
        text = extract_text(block.get('html', ''))
        text_lower = text.lower()
        if text_lower.startswith('keywords:') or text_lower.startswith('mots clés:') or text_lower.startswith('mots-clés:'):
            keywords = re.sub(r'^(keywords|mots[\s-]?clés)\s*:\s*', '', text, flags=re.IGNORECASE)
            if 'mots' in text_lower:
                result['keywords_fr'] = keywords
            else:
                result['keywords'] = keywords

    # --- Build body HTML ---
    # Find where body starts (after abstract) and ends (before references)
    body_blocks = []
    in_body = False
    in_references = False
    in_acknowledgements = False

    for block in blocks:
        bt = block.get('block_type', '')
        html = block.get('html', '')
        text = extract_text(html)

        # Skip page headers/footers
        if bt in ('PageHeader', 'PageFooter'):
            continue

        if bt == 'SectionHeader':
            text_check = text.lower().strip()

            # Check for references section
            if matches_section(text, 'references'):
                in_body = False
                in_references = True
                continue

            # Check for acknowledgements
            if matches_section(text, 'acknowledgements'):
                in_body = False
                in_acknowledgements = True
                continue

            # Check for abstract (skip)
            if matches_section(text, 'abstract'):
                in_body = False
                continue

            # Check for body start markers
            # Body starts after abstract with numbered sections OR common section names
            body_starters = ['introduction', 'background', 'methods', 'method', 'contexte', 'méthodes',
                            'study 1', 'study 2', 'experiment 1', 'literature review', 'theoretical background']
            if re.match(r'^1\.?\s', text) or text_check in body_starters or re.match(r'^(study|experiment)\s*\d', text_check):
                in_body = True

            if in_body:
                body_blocks.append(html)

        elif bt == 'ListGroup' and in_references:
            # References come as <ol>/<li> OR as <p> elements in a ListGroup
            soup = BeautifulSoup(html, 'html.parser')
            # Try <li> first (numbered lists)
            li_items = soup.find_all('li')
            if li_items:
                for li in li_items:
                    ref_text = li.get_text(strip=True)
                    if ref_text:
                        result['references'].append(ref_text)
            else:
                # Fall back to <p> elements (paragraph-based references)
                for p in soup.find_all('p'):
                    ref_text = p.get_text(strip=True)
                    if ref_text and len(ref_text) > 20:  # Skip short fragments
                        result['references'].append(ref_text)

        elif bt == 'Text':
            if in_references:
                # Extract reference (sometimes as individual text blocks)
                if re.match(r'^\d+\.?\s', text) or re.match(r'^\[?\d+\]?\s', text):
                    result['references'].append(text)
            elif in_acknowledgements:
                if not result['acknowledgements']:
                    result['acknowledgements'] = text
                else:
                    result['acknowledgements'] += ' ' + text
            elif in_body:
                body_blocks.append(html)

        elif bt == 'Table' and in_body:
            body_blocks.append(html)
            result['tables'].append({
                'page': block.get('page'),
                'html': html[:500] + '...' if len(html) > 500 else html
            })

        elif bt == 'Figure' and in_body:
            body_blocks.append(html)
            result['figures'].append({
                'page': block.get('page'),
                'html': html[:200] + '...' if len(html) > 200 else html
            })

        elif bt == 'Footnote':
            # Check for author email in footnotes
            if '@' in text and not result.get('author_email'):
                email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', text)
                if email_match:
                    result['author_email'] = email_match.group(0)

    result['body_html'] = '\n'.join(body_blocks)

    # --- Warnings ---
    if not result['title']:
        result['warnings'].append('[MISSING] No title found')
    if not result['abstract']:
        result['warnings'].append('[MISSING] No abstract found')
    if not result['doi']:
        result['warnings'].append('[MISSING] No DOI found in page footers')
    if not result['references']:
        result['warnings'].append('[MISSING] No references extracted')

    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python parse_article_blocks.py <json_file>")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    result = parse_blocks(json_path)

    # Print summary
    print(f"=== {result['source_file']} ===\n")
    print(f"Title: {result['title']}")
    print(f"Article Type: {result['article_type']}")
    print(f"Authors: {result['authors']}")
    print(f"Citation: {result['citation']}")
    print(f"DOI: {result['doi']}")
    print(f"Year: {result['year']}")
    print(f"\nAbstract ({len(result['abstract'] or '')} chars)")
    if result['abstract']:
        print(f"  {result['abstract'][:200]}...")
    if result['abstract_fr']:
        print(f"Abstract FR: {result['abstract_fr'][:100]}...")
    if result['abstract_en']:
        print(f"Abstract EN: {result['abstract_en'][:100]}...")
    print(f"\nKeywords: {result['keywords']}")
    print(f"Keywords FR: {result['keywords_fr']}")
    print(f"\nBody HTML: {len(result['body_html'] or '')} chars")
    print(f"Acknowledgements: {'Yes' if result['acknowledgements'] else 'No'}")
    print(f"References: {len(result['references'])}")
    print(f"Figures: {len(result['figures'])}")
    print(f"Tables: {len(result['tables'])}")

    print(f"\n--- Page Headers ({len(result['page_headers'])}) ---")
    for h in result['page_headers'][:5]:
        print(f"  Page {h['page']}: {h['text'][:80]}")

    print(f"\n--- Page Footers ({len(result['page_footers'])}) ---")
    for f in result['page_footers'][:5]:
        print(f"  Page {f['page']}: {f['text'][:80]}")

    if result['warnings']:
        print(f"\n--- Warnings ({len(result['warnings'])}) ---")
        for w in result['warnings']:
            print(f"  {w}")

    # Save result
    output_path = json_path.with_name(json_path.stem + '_parsed.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        output = {**result, 'body_html': f"[{len(result['body_html'] or '')} chars]"}
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to: {output_path}")


if __name__ == '__main__':
    main()
