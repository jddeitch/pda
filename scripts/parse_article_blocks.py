#!/usr/bin/env python3
"""
Parse Datalab structured JSON blocks into article components.

Three-pass approach:
1. CLASSIFY: Tag each block with its section based on h2 headers
2. CLEAN: Join split sentences, associate captions with tables/figures, remove cruft
3. REASSEMBLE: Build full document in correct order

Key principle: Process EVERY block. Nothing gets silently dropped.
If we can't classify something, it goes in 'unclassified' for human review.

Datalab block types we handle:
- SectionHeader: h1/h2/h3/h4/h5/h6 headings
- Text: Paragraphs
- Table: HTML tables
- Figure/Picture: Images
- Caption: Table/figure captions
- ListGroup: Lists (often references)
- PageHeader/PageFooter: Skip (page chrome)
- Footnote: Author info, emails
"""

import re
import json
import sys
from pathlib import Path
from bs4 import BeautifulSoup
from collections import defaultdict


# --- Section detection patterns (multilingual) ---

SECTION_PATTERNS = {
    'abstract': ['abstract', 'summary', 'résumé', 'resume'],
    'keywords': ['keywords', 'mots-clés', 'mots clés', 'key words'],
    'references': ['references', 'bibliography', 'références', 'bibliographie', 'literature cited'],
    'acknowledgements': ['acknowledgements', 'acknowledgments', 'remerciements'],
    'appendix': ['appendix', 'appendices', 'annexe', 'annexes', 'supplementary', 'supplemental', 'supporting information'],
    'conflict_of_interest': ['conflict of interest', 'conflicts of interest', 'competing interests', 'déclaration', 'disclosure'],
    'author_contributions': ['author contributions', 'contributions', 'contributorship'],
    'funding': ['funding', 'financial support', 'financement'],
    'data_availability': ['data availability', 'data statement'],
}

# Body section names - these are all part of main content
BODY_SECTIONS = [
    'introduction', 'background', 'literature review', 'theoretical background',
    'methods', 'method', 'methodology', 'materials and methods', 'participants', 'procedure',
    'results', 'findings',
    'discussion', 'conclusions', 'conclusion', 'summary and conclusions',
    'limitations', 'future directions', 'implications',
    # French
    'contexte', 'méthodes', 'méthodologie', 'résultats', 'discussion', 'conclusions',
]


def extract_text(html: str) -> str:
    """Extract plain text from HTML."""
    if not html:
        return ''
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text(strip=True)


def extract_tag_level(html: str) -> str | None:
    """Extract the HTML tag (h1, h2, etc.) from a SectionHeader block."""
    match = re.match(r'<(h[1-6])', html, re.IGNORECASE)
    return match.group(1).lower() if match else None


def identify_section(text: str) -> str | None:
    """
    Identify which major section a header belongs to.
    Returns section name or None if it's a body subsection.
    """
    text_lower = text.lower().strip()

    # Check each section type
    for section, patterns in SECTION_PATTERNS.items():
        for pattern in patterns:
            if pattern in text_lower:
                return section

    # Check if it's a body section (these all stay as 'body')
    for body_pattern in BODY_SECTIONS:
        if body_pattern in text_lower:
            return 'body'

    # Check for numbered section start (e.g., "1. Introduction", "1 Methods")
    if re.match(r'^\d+\.?\s+\w', text):
        return 'body'

    return None


def extract_doi(text: str) -> str | None:
    """Extract DOI from text if present."""
    doi_match = re.search(r'10\.\d{4,}/[^\s,<>\"]+', text)
    if doi_match:
        doi = doi_match.group(0).rstrip('.,;:)"\'')
        return doi
    return None


def extract_year(text: str) -> str | None:
    """Extract 4-digit year from text."""
    match = re.search(r'\b(19|20)\d{2}\b', text)
    return match.group(0) if match else None


def is_article_type_label(text: str) -> bool:
    """Check if text is an article type label."""
    labels = [
        'REVIEW', 'RESEARCH ARTICLE', 'ORIGINAL ARTICLE', 'COMMENTARY',
        'CASE REPORT', 'LETTER', 'EDITORIAL', 'SHORT COMMUNICATION',
        'REVUE DE LITTÉRATURE', 'ARTICLE ORIGINAL', 'CAS CLINIQUE',
    ]
    return text.upper().strip() in labels


def is_sentence_continuation(text: str) -> bool:
    """Check if text appears to be a continuation (starts with lowercase)."""
    text = text.strip()
    return bool(text) and text[0].islower()


def is_incomplete_sentence(text: str) -> bool:
    """Check if text appears to be incomplete (doesn't end with sentence punctuation)."""
    text = text.strip()
    return bool(text) and text[-1] not in '.!?:;'


def join_split_sentences(blocks: list[dict]) -> list[dict]:
    """
    Join sentences that were split across page/column breaks.
    Returns a new list of blocks with split sentences joined.
    """
    result = []
    i = 0
    consumed = set()

    while i < len(blocks):
        if i in consumed:
            i += 1
            continue

        block = blocks[i]
        bt = block.get('block_type', '')

        if bt in ['Text', 'Caption']:
            text = extract_text(block.get('html', ''))
            current_incomplete = is_incomplete_sentence(text)

            # Look ahead for a continuation
            j = i + 1
            continuation_idx = None
            crossed_page = False

            while j < len(blocks):
                next_block = blocks[j]
                next_bt = next_block.get('block_type', '')

                if next_bt in ['PageFooter', 'PageHeader']:
                    crossed_page = True
                    j += 1
                    continue

                if next_bt == 'Table':
                    j += 1
                    continue

                if next_bt in ['Figure', 'Picture']:
                    break

                if next_bt in ['Text', 'Caption']:
                    next_text = extract_text(next_block.get('html', ''))

                    # Skip obvious table/figure captions
                    if next_bt == 'Caption' and re.match(r'^(Table|Fig\.?|Figure)\s*\d', next_text, re.IGNORECASE):
                        j += 1
                        continue

                    if is_sentence_continuation(next_text):
                        continuation_idx = j
                        break

                    if current_incomplete and not crossed_page and block.get('page') == next_block.get('page'):
                        continuation_idx = j
                        break

                    if crossed_page:
                        j += 1
                        continue

                    break

                break

            if continuation_idx is not None:
                continuation_text = extract_text(blocks[continuation_idx].get('html', ''))
                joined_text = text + ' ' + continuation_text
                new_block = block.copy()
                new_block['html'] = f'<p>{joined_text}</p>'
                new_block['_joined'] = True
                result.append(new_block)
                consumed.add(continuation_idx)
                i += 1
                continue

        result.append(block)
        i += 1

    return result


def parse_blocks(json_path: Path) -> dict:
    """
    Parse Datalab JSON blocks into structured article components.

    Three-pass approach:
    1. Classify each block by section
    2. Clean up (join sentences, associate captions, remove cruft)
    3. Reassemble in document order
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    raw_blocks = data.get('blocks', [])
    input_block_count = len(raw_blocks)

    # --- Pass 1: Clean up split sentences ---
    blocks = join_split_sentences(raw_blocks)

    # --- Pass 2: Classify each block by section ---

    # Track current section as we walk through
    current_section = 'preamble'  # Before abstract

    # Collect blocks by section
    sections = defaultdict(list)

    # Metadata extracted along the way
    metadata = {
        'title': None,
        'authors': None,
        'article_type': None,
        'doi': None,
        'year': None,
        'citation': None,
        'author_email': None,
    }

    # Track tables and figures with their context
    tables = []
    figures = []

    # Track references separately
    references = []

    # Pending caption (to associate with next table/figure)
    pending_caption = None

    # Statistics for verification
    classified_count = 0
    skipped_count = 0  # PageHeader/PageFooter

    for i, block in enumerate(blocks):
        bt = block.get('block_type', '')
        html = block.get('html', '')
        text = extract_text(html)
        page = block.get('page', 0)

        # Skip page chrome
        if bt in ('PageHeader', 'PageFooter'):
            skipped_count += 1
            # But extract metadata from them
            if bt == 'PageFooter' and not metadata['doi']:
                metadata['doi'] = extract_doi(html)
            if bt == 'PageHeader':
                # Look for citation pattern
                if not metadata['citation'] and (re.search(r'\d{4}.*\d+[-–]\d+', text) or re.search(r'\(\d{4}\)', text)):
                    metadata['citation'] = text
                    if not metadata['year']:
                        metadata['year'] = extract_year(text)
            continue

        classified_count += 1

        # Handle section headers
        if bt == 'SectionHeader':
            tag = extract_tag_level(html)

            # h1 is usually the title
            if tag == 'h1' and page == 0 and not metadata['title']:
                metadata['title'] = text
                sections['preamble'].append(block)
                continue

            # h2 marks major sections - check if it's a known section type
            if tag == 'h2':
                detected = identify_section(text)
                if detected:
                    current_section = detected
                # If not detected, it's a custom h2 in current section - keep current_section

            # h3/h4/h5/h6 are subsections - stay in current section

            # Add the header to its section
            sections[current_section].append(block)
            continue

        # Handle captions - save for next table/figure
        if bt == 'Caption':
            # NOTE: Captions like "Appendix 5-1: ..." are REFERENCES to appendix tables,
            # not the start of the appendix section. Only h2 headers mark section changes.
            # The appendix section itself will have an h2 "Appendix" or similar.

            pending_caption = {
                'text': text,
                'html': html,
                'page': page,
                'section': current_section
            }
            # Also add to section content
            sections[current_section].append(block)
            continue

        # Handle tables
        if bt == 'Table':
            table_entry = {
                'html': html,
                'page': page,
                'section': current_section,
                'caption': pending_caption['text'] if pending_caption else None
            }
            tables.append(table_entry)
            pending_caption = None
            sections[current_section].append(block)
            continue

        # Handle figures
        if bt in ('Figure', 'Picture'):
            figure_entry = {
                'html': html[:500] if len(html) > 500 else html,  # Truncate large base64
                'page': page,
                'section': current_section,
                'caption': pending_caption['text'] if pending_caption else None
            }
            figures.append(figure_entry)
            pending_caption = None
            sections[current_section].append(block)
            continue

        # Handle list groups (often references)
        if bt == 'ListGroup':
            if current_section == 'references':
                # Extract individual references
                soup = BeautifulSoup(html, 'html.parser')
                for li in soup.find_all('li'):
                    ref_text = li.get_text(strip=True)
                    if ref_text:
                        references.append(ref_text)
                for p in soup.find_all('p'):
                    ref_text = p.get_text(strip=True)
                    if ref_text and len(ref_text) > 20:
                        references.append(ref_text)
            sections[current_section].append(block)
            continue

        # Handle text blocks
        if bt == 'Text':
            # Extract metadata from preamble
            if current_section == 'preamble' and page == 0:
                # Look for authors
                if not metadata['authors'] and re.search(r'[A-Z]\.\s*[A-Z][a-z]+.*,', text) and len(text) < 300:
                    metadata['authors'] = text
                # Look for year
                if not metadata['year']:
                    for pattern in [r'©.*?(20\d{2})', r'published[:\s]+.*?(20\d{2})', r'\((20\d{2})\)']:
                        match = re.search(pattern, text, re.IGNORECASE)
                        if match:
                            metadata['year'] = match.group(1)
                            break

            # Check for inline keywords
            text_lower = text.lower()
            if text_lower.startswith('keywords:') or text_lower.startswith('mots'):
                current_section = 'keywords'

            # Individual reference lines
            if current_section == 'references':
                if re.match(r'^\d+\.?\s', text) or re.match(r'^\[?\d+\]?\s', text):
                    references.append(text)

            sections[current_section].append(block)
            continue

        # Handle footnotes
        if bt == 'Footnote':
            if '@' in text and not metadata['author_email']:
                email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', text)
                if email_match:
                    metadata['author_email'] = email_match.group(0)
            sections[current_section].append(block)
            continue

        # Any other block type - add to current section
        sections[current_section].append(block)

    # --- Pass 3: Reassemble document ---

    def blocks_to_html(block_list: list[dict]) -> str:
        """Convert list of blocks to HTML string."""
        return '\n'.join(b.get('html', '') for b in block_list)

    def extract_abstract(block_list: list[dict]) -> tuple[str, str | None]:
        """Extract abstract text and detect language."""
        parts = []
        lang = None
        for b in block_list:
            bt = b.get('block_type', '')
            text = extract_text(b.get('html', ''))
            if bt == 'SectionHeader':
                text_lower = text.lower()
                if 'résumé' in text_lower:
                    lang = 'fr'
                elif 'abstract' in text_lower:
                    lang = 'en'
            elif bt == 'Text':
                parts.append(text)
        return ' '.join(parts), lang

    # Build abstract
    abstract_text, abstract_lang = extract_abstract(sections.get('abstract', []))

    # Build body HTML - main content sections in order
    # References are extracted separately (not in body_html)
    # COI goes at the very end (after appendices)
    body_sections_order = [
        'body',                    # Main content (Intro, Methods, Results, Discussion)
        'acknowledgements',        # Often before references in original
        'funding',
        'author_contributions',
        'data_availability',
        'appendix',                # Supplementary material, tables, questionnaires
        'conflict_of_interest',    # At the very end
    ]
    body_parts = []
    for sec in body_sections_order:
        if sec in sections and sections[sec]:
            body_parts.append(blocks_to_html(sections[sec]))

    body_html = '\n'.join(body_parts)

    # Extract keywords
    keywords = None
    keywords_fr = None
    for b in sections.get('keywords', []):
        text = extract_text(b.get('html', ''))
        text_lower = text.lower()
        if text_lower.startswith('keywords:'):
            keywords = re.sub(r'^keywords:\s*', '', text, flags=re.IGNORECASE)
        elif 'mots' in text_lower:
            keywords_fr = re.sub(r'^mots[\s-]?clés\s*:\s*', '', text, flags=re.IGNORECASE)

    # Extract acknowledgements text
    ack_parts = []
    for b in sections.get('acknowledgements', []):
        if b.get('block_type') == 'Text':
            ack_parts.append(extract_text(b.get('html', '')))
    acknowledgements = ' '.join(ack_parts) if ack_parts else None

    # Extract conflict of interest text
    coi_parts = []
    for b in sections.get('conflict_of_interest', []):
        if b.get('block_type') == 'Text':
            coi_parts.append(extract_text(b.get('html', '')))
    conflict_of_interest = ' '.join(coi_parts) if coi_parts else None

    # Build warnings
    warnings = []
    if not metadata['title']:
        warnings.append('[MISSING] No title found')
    if not abstract_text:
        warnings.append('[MISSING] No abstract found')
    if not metadata['doi']:
        warnings.append('[MISSING] No DOI found')
    if not references:
        warnings.append('[MISSING] No references extracted')

    # Check for unclassified content (blocks in preamble that aren't title/authors)
    # Preamble should only contain title (first h1) - anything else is unclassified
    unclassified_blocks = []
    for b in sections.get('preamble', []):
        bt = b.get('block_type', '')
        text = extract_text(b.get('html', ''))
        # Skip the title (first h1 we captured)
        if bt == 'SectionHeader' and text == metadata.get('title'):
            continue
        # Everything else in preamble is unclassified
        unclassified_blocks.append({
            'block_type': bt,
            'text': text[:200] + '...' if len(text) > 200 else text,
            'page': b.get('page', 0)
        })

    if unclassified_blocks:
        warnings.append(f'[UNCLASSIFIED] {len(unclassified_blocks)} blocks in preamble not categorized')

    # Build result
    result = {
        'source_file': json_path.name,

        # Metadata
        'title': metadata['title'],
        'authors': metadata['authors'],
        'article_type': metadata['article_type'],
        'doi': metadata['doi'],
        'year': metadata['year'],
        'citation': metadata['citation'],
        'author_email': metadata['author_email'],

        # Abstract
        'abstract': abstract_text or None,
        'abstract_en': abstract_text if abstract_lang == 'en' else None,
        'abstract_fr': abstract_text if abstract_lang == 'fr' else None,

        # Keywords
        'keywords': keywords,
        'keywords_fr': keywords_fr,

        # Main content - reassembled in order
        'body_html': body_html,

        # Extracted sections
        'conflict_of_interest': conflict_of_interest,
        'acknowledgements': acknowledgements,
        'references': references,

        # Tables and figures with context
        'tables': tables,
        'figures': figures,

        # Section inventory (for debugging)
        'sections_found': {k: len(v) for k, v in sections.items()},

        # Unclassified blocks (for human review)
        'unclassified': unclassified_blocks,

        # Verification stats
        'stats': {
            'input_blocks': input_block_count,
            'after_join': len(blocks),
            'classified': classified_count,
            'skipped_chrome': skipped_count,
        },

        'warnings': warnings,
    }

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
    print(f"Authors: {result['authors']}")
    print(f"Year: {result['year']}")
    print(f"DOI: {result['doi']}")
    print(f"Citation: {result['citation']}")

    print(f"\nAbstract: {len(result['abstract'] or '')} chars")
    if result['abstract']:
        print(f"  {result['abstract'][:200]}...")

    print(f"\nKeywords: {result['keywords']}")
    print(f"Keywords FR: {result['keywords_fr']}")

    print(f"\nBody HTML: {len(result['body_html'] or '')} chars")
    print(f"Conflict of Interest: {result['conflict_of_interest']}")
    print(f"Acknowledgements: {'Yes' if result['acknowledgements'] else 'No'}")
    print(f"References: {len(result['references'])}")
    print(f"Tables: {len(result['tables'])}")
    print(f"Figures: {len(result['figures'])}")

    print(f"\n--- Sections Found ---")
    for sec, count in sorted(result['sections_found'].items()):
        print(f"  {sec}: {count} blocks")

    print(f"\n--- Stats ---")
    for k, v in result['stats'].items():
        print(f"  {k}: {v}")

    if result['warnings']:
        print(f"\n--- Warnings ({len(result['warnings'])}) ---")
        for w in result['warnings']:
            print(f"  {w}")

    # Save result
    output_path = json_path.with_name(json_path.stem + '_parsed.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to: {output_path}")


if __name__ == '__main__':
    main()
