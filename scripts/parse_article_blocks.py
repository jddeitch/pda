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
import yaml
import base64
from pathlib import Path
from bs4 import BeautifulSoup
from collections import defaultdict


# --- Normalize Datalab JSON formats ---

def normalize_datalab_json(data: dict) -> list:
    """
    Normalize Datalab JSON to flat block list.

    API via poll_and_save(): { "blocks": [...] }
    Manual website downloads: { "children": [Pages...] } where each Page has "children" with blocks

    Returns flat list of blocks. Structure and content are identical — only difference
    is manual downloads have filename refs in HTML src while API embeds base64.
    The images dict (with base64) exists in both, which is what the parser uses.
    """
    # Already flattened (from poll_and_save)
    if 'blocks' in data:
        return data['blocks']

    # Hierarchical format (manual download) — flatten Pages
    if 'children' in data:
        blocks = []
        for page in data['children']:
            if page.get('block_type') == 'Page':
                blocks.extend(page.get('children', []))
        return blocks

    return []


# --- Load section patterns from YAML ---

def load_section_headings():
    """Load section heading patterns from data/section_headings.yaml"""
    yaml_path = Path(__file__).parent.parent / 'data' / 'section_headings.yaml'
    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    # Build SECTION_PATTERNS: flatten all language variants into single list per section
    section_patterns = {}
    for section_name in ['abstract', 'keywords', 'references', 'acknowledgements',
                         'appendix', 'conflict_of_interest', 'author_contributions',
                         'funding', 'data_availability', 'corresponding_author']:
        if section_name in data:
            patterns = []
            for lang_patterns in data[section_name].values():
                patterns.extend(lang_patterns)
            section_patterns[section_name] = patterns

    # Build BODY_SECTIONS: flatten all language variants
    body_sections = []
    if 'body_sections' in data:
        for lang_patterns in data['body_sections'].values():
            body_sections.extend(lang_patterns)

    # Build METADATA_SECTIONS: flatten all language variants
    metadata_sections = set()
    if 'metadata_sections' in data:
        for lang_patterns in data['metadata_sections'].values():
            metadata_sections.update(lang_patterns)

    return section_patterns, body_sections, metadata_sections


SECTION_PATTERNS, BODY_SECTIONS, METADATA_SECTIONS = load_section_headings()


def convert_math_tags(html: str) -> str:
    """
    Convert <math> tags to displayable content.

    Datalab wraps formulas in <math> tags, but browsers expect proper MathML
    inside them. Since these are simple inline formulas (not complex equations),
    we convert LaTeX to Unicode and wrap in a styled span instead.
    """
    # Common LaTeX symbols that appear in academic papers
    latex_to_unicode = {
        r'\times': '×',
        r'\div': '÷',
        r'\pm': '±',
        r'\leq': '≤',
        r'\geq': '≥',
        r'\neq': '≠',
        r'\approx': '≈',
        r'\alpha': 'α',
        r'\beta': 'β',
        r'\gamma': 'γ',
        r'\delta': 'δ',
        r'\chi': 'χ',
        r'\eta': 'η',
        r'\mu': 'μ',
        r'\sigma': 'σ',
        r'\sum': 'Σ',
        r'\infty': '∞',
    }

    def replace_math(match):
        content = match.group(1)
        # Convert any LaTeX commands to Unicode
        for latex, unicode_char in latex_to_unicode.items():
            content = content.replace(latex, unicode_char)
        # Use a span with class for styling - browsers can't render plain text in <math>
        return f'<span class="formula">{content}</span>'

    return re.sub(r'<math>(.*?)</math>', replace_math, html)


def extract_text(html: str, preserve_math: bool = False) -> str:
    """
    Extract plain text from HTML.

    Args:
        html: HTML string to extract text from
        preserve_math: If True, preserve <math>...</math> tags inline
    """
    if not html:
        return ''

    if preserve_math:
        # Preserve math tags by replacing them with placeholders, then restoring
        math_tags = []
        def save_math(match):
            math_tags.append(match.group(0))
            return f'__MATH_{len(math_tags) - 1}__'

        html_with_placeholders = re.sub(r'<math>.*?</math>', save_math, html)
        soup = BeautifulSoup(html_with_placeholders, 'html.parser')
        text = soup.get_text(strip=True)

        # Restore math tags
        for i, math_tag in enumerate(math_tags):
            text = text.replace(f'__MATH_{i}__', math_tag)
        return text

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
    """Check if text appears to be a continuation (starts with lowercase).

    Skips leading punctuation like quotes/apostrophes since academic text
    often has quoted terms at sentence boundaries (e.g., "'atypical' range").
    """
    text = text.strip()
    if not text:
        return False
    # Skip leading quotes/apostrophes to find the first letter
    for char in text:
        if char.isalpha():
            return char.islower()
        if char not in '\'"\u2018\u2019\u201C\u201D':  # Straight and curly quotes
            break
    return False


def is_incomplete_sentence(text: str) -> bool:
    """Check if text appears to be incomplete (doesn't end with sentence punctuation)."""
    text = text.strip()
    return bool(text) and text[-1] not in '.!?:;'


def join_split_sentences(blocks: list[dict]) -> list[dict]:
    """
    Join sentences that were split across page/column breaks.
    Returns a new list of blocks with split sentences joined.

    Key insight: Datalab correctly tags page chrome (PageHeader, PageFooter),
    footnotes, figures, tables, and captions. When looking for a continuation
    of an incomplete sentence, we skip over these non-content blocks BUT
    stop if we hit a SectionHeader (which marks a new section boundary).
    """
    # Block types to skip when looking for continuation
    SKIP_BLOCK_TYPES = {'PageHeader', 'PageFooter', 'Figure', 'Picture', 'Caption', 'Footnote', 'Table'}

    # METADATA_SECTIONS loaded from data/section_headings.yaml at module level

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
            text = extract_text(block.get('html', ''), preserve_math=True)
            current_incomplete = is_incomplete_sentence(text)

            # Look ahead for a continuation
            j = i + 1
            continuation_idx = None
            crossed_page = False
            crossed_section = False  # Stop if we hit a new section

            in_metadata_section = False  # Track if we're inside a metadata section

            while j < len(blocks):
                next_block = blocks[j]
                next_bt = next_block.get('block_type', '')

                # SectionHeader marks a section boundary
                if next_bt == 'SectionHeader':
                    next_text = extract_text(next_block.get('html', ''))
                    next_text_lower = next_text.lower().strip()

                    # Metadata sections (like "Corresponding author") can be skipped
                    # because they're not body content
                    if any(meta in next_text_lower for meta in METADATA_SECTIONS):
                        in_metadata_section = True
                        j += 1
                        continue

                    # Other SectionHeaders mark real section boundaries - don't join across
                    in_metadata_section = False
                    crossed_section = True
                    j += 1
                    continue

                # Skip over non-content blocks (page chrome, figures, footnotes, etc.)
                if next_bt in SKIP_BLOCK_TYPES:
                    if next_bt in ['PageHeader', 'PageFooter']:
                        crossed_page = True
                        in_metadata_section = False  # Reset on page change
                    j += 1
                    continue

                if next_bt in ['Text', 'Caption']:
                    # Skip blocks already consumed by an earlier join
                    if j in consumed:
                        j += 1
                        continue

                    # Skip text blocks inside metadata sections (like author address/email)
                    if in_metadata_section:
                        j += 1
                        continue

                    # Don't join across section boundaries
                    if crossed_section:
                        break

                    next_text = extract_text(next_block.get('html', ''), preserve_math=True)

                    # Skip obvious table/figure captions that start with "Table 1" etc.
                    if next_bt == 'Caption' and re.match(r'^(Table|Fig\.?|Figure)\s*\d', next_text, re.IGNORECASE):
                        j += 1
                        continue

                    # Found a continuation if it starts with lowercase
                    if is_sentence_continuation(next_text):
                        continuation_idx = j
                        break

                    # Or if current is incomplete and we're on the same page (column break)
                    if current_incomplete and not crossed_page and block.get('page') == next_block.get('page'):
                        continuation_idx = j
                        break

                    # If we crossed a page, keep looking (the continuation might be after more chrome)
                    if crossed_page:
                        j += 1
                        continue

                    # Not a continuation, stop looking
                    break

                # Unknown block type, stop looking
                break

            if continuation_idx is not None:
                continuation_text = extract_text(blocks[continuation_idx].get('html', ''), preserve_math=True)
                joined_text = text + ' ' + continuation_text
                consumed.add(continuation_idx)

                # Keep joining if the result is still incomplete and there are more continuations
                # This handles chains like: "text," + "more text with a" + "continuation."
                while is_incomplete_sentence(joined_text):
                    # Look for another continuation starting after the last one we consumed
                    next_j = continuation_idx + 1
                    next_continuation_idx = None
                    next_crossed_page = crossed_page
                    next_in_metadata_section = in_metadata_section

                    while next_j < len(blocks):
                        next_block = blocks[next_j]
                        next_bt = next_block.get('block_type', '')

                        if next_bt == 'SectionHeader':
                            next_text_check = extract_text(next_block.get('html', ''))
                            if any(meta in next_text_check.lower().strip() for meta in METADATA_SECTIONS):
                                next_in_metadata_section = True
                                next_j += 1
                                continue
                            # Hit a real section boundary, stop
                            break

                        if next_bt in SKIP_BLOCK_TYPES:
                            if next_bt in ['PageHeader', 'PageFooter']:
                                next_crossed_page = True
                                next_in_metadata_section = False
                            next_j += 1
                            continue

                        if next_bt in ['Text', 'Caption']:
                            # Skip blocks already consumed
                            if next_j in consumed:
                                next_j += 1
                                continue

                            if next_in_metadata_section:
                                next_j += 1
                                continue

                            next_text_candidate = extract_text(next_block.get('html', ''), preserve_math=True)

                            if is_sentence_continuation(next_text_candidate):
                                next_continuation_idx = next_j
                                break

                            # Not a continuation, stop looking
                            break

                        # Unknown block type
                        break

                    if next_continuation_idx is not None:
                        next_continuation_text = extract_text(blocks[next_continuation_idx].get('html', ''), preserve_math=True)
                        joined_text = joined_text + ' ' + next_continuation_text
                        consumed.add(next_continuation_idx)
                        continuation_idx = next_continuation_idx
                        crossed_page = next_crossed_page
                        in_metadata_section = next_in_metadata_section
                    else:
                        # No more continuations found
                        break

                new_block = block.copy()
                new_block['html'] = f'<p>{joined_text}</p>'
                new_block['_joined'] = True
                result.append(new_block)
                i += 1
                continue

        result.append(block)
        i += 1

    return result


def parse_blocks(json_path: Path, images_dir: Path | None = None) -> dict:
    """
    Parse Datalab JSON blocks into structured article components.

    Three-pass approach:
    1. Classify each block by section
    2. Clean up (join sentences, associate captions, remove cruft)
    3. Reassemble in document order

    Args:
        json_path: Path to Datalab JSON file
        images_dir: Directory to save extracted images (created if needed)
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    raw_blocks = normalize_datalab_json(data)
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

    # Track footnotes separately (author affiliations, emails, etc.)
    # These become endnotes in the output, not mixed into body_html
    footnotes = []

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

        # Handle figures - extract images to files
        if bt in ('Figure', 'Picture'):
            figure_id = f"fig_{len(figures) + 1}"
            figure_entry = {
                'id': figure_id,
                'page': page,
                'section': current_section,
                'caption': pending_caption['text'] if pending_caption else None,
                'alt': None,
                'description_html': None,  # Full description including tables
                'images': [],  # List of saved image filenames
            }

            # Parse the figure HTML
            soup = BeautifulSoup(html, 'html.parser')

            # Extract alt text from img tag
            img_tag = soup.find('img')
            if img_tag and img_tag.get('alt'):
                figure_entry['alt'] = img_tag.get('alt')

            # Extract the description div - contains paragraphs AND data tables
            # This is valuable content that explains the figure
            desc_div = soup.find('div', class_='img-description')
            if desc_div:
                figure_entry['description_html'] = str(desc_div)

            # Extract and save images from the block's 'images' dict
            block_images = block.get('images', {})
            for img_filename, img_base64 in block_images.items():
                if images_dir:
                    images_dir.mkdir(parents=True, exist_ok=True)
                    # Use figure_id prefix for cleaner filenames
                    ext = Path(img_filename).suffix or '.jpg'
                    saved_filename = f"{figure_id}{ext}"
                    img_path = images_dir / saved_filename
                    try:
                        img_data = base64.b64decode(img_base64)
                        with open(img_path, 'wb') as img_file:
                            img_file.write(img_data)
                        figure_entry['images'].append(saved_filename)
                    except Exception as e:
                        warnings.append(f"[IMAGE] Failed to save {img_filename}: {e}")
                else:
                    # No output dir - just note the filename
                    figure_entry['images'].append(img_filename)

            figures.append(figure_entry)
            pending_caption = None

            # Add placeholder to section for document order
            placeholder_block = {
                'block_type': 'FigurePlaceholder',
                'figure_id': figure_id,
                'page': page,
            }
            sections[current_section].append(placeholder_block)
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

        # Handle footnotes - collect separately, don't add to sections
        # These are author affiliations, corresponding author info, etc.
        # They'll be converted to endnotes in the output
        if bt == 'Footnote':
            if '@' in text and not metadata['author_email']:
                email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', text)
                if email_match:
                    metadata['author_email'] = email_match.group(0)
            footnotes.append({
                'text': text,
                'html': html,
                'page': page,
            })
            continue

        # Any other block type - add to current section
        sections[current_section].append(block)

    # --- Pass 3: Reassemble document ---

    def blocks_to_html(block_list: list[dict]) -> str:
        """Convert list of blocks to HTML string, with figure placeholders."""
        parts = []
        for b in block_list:
            bt = b.get('block_type', '')
            if bt == 'FigurePlaceholder':
                # Insert a placeholder div that can be replaced with actual figure
                fig_id = b.get('figure_id', 'unknown')
                parts.append(f'<div class="figure-placeholder" data-figure-id="{fig_id}"></div>')
            else:
                html = b.get('html', '')
                # Convert math tags to styled spans with Unicode
                html = convert_math_tags(html)
                parts.append(html)
        return '\n'.join(parts)

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

        # Footnotes (author affiliations, emails) - kept separate for endnotes
        'footnotes': footnotes,

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
            'footnotes_extracted': len(footnotes),
        },

        'warnings': warnings,
    }

    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python parse_article_blocks.py <json_file>")
        sys.exit(1)

    json_path = Path(sys.argv[1])

    # Create images directory alongside the JSON file
    slug = json_path.stem.replace('_parsed', '')
    images_dir = json_path.parent / 'images' / slug

    result = parse_blocks(json_path, images_dir=images_dir)

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
    for fig in result['figures']:
        print(f"  - {fig['id']}: {len(fig['images'])} image(s), caption: {fig['caption'][:50] if fig['caption'] else 'None'}...")
    print(f"Footnotes: {len(result['footnotes'])}")

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
