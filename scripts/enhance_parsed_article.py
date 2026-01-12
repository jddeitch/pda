#!/usr/bin/env python3
"""
Step 4: AI Enhancement Pass

This script is run by Claude after reviewing the mechanical parser output.
It applies corrections that require judgment (missing authors, malformed
citations, classification, etc.).

Two modes:
- --suggest: Auto-detect and print suggestions (uses CrossRef, heuristics)
- --apply: Apply suggestions AND/OR manual overrides

Manual overrides (Claude extracts these from reading the raw HTML):
    --authors "E. O'Nions, J. Gould, ..."
    --year "2015"
    --citation "Eur Child Adolesc Psychiatry (2015) 24:1–13"
    --title "New title"
    --abstract "New abstract"
    --acknowledgements "Thanks to..."
    --keywords "autism, PDA, demand avoidance"

Classification overrides:
    --method empirical|synthesis|theoretical|lived_experience
    --voice academic|practitioner|organization|individual
    --peer-reviewed (flag)

Usage:
    python enhance_parsed_article.py <json_path> --suggest
    python enhance_parsed_article.py <json_path> --apply
    python enhance_parsed_article.py <json_path> --authors "A. Smith" --year 2018
    python enhance_parsed_article.py <json_path> --apply --authors "A. Smith" --citation "..."
"""

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

import yaml


def load_json(json_path: Path) -> dict:
    """Load the parsed article JSON."""
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(json_path: Path, data: dict) -> None:
    """Save the updated JSON."""
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_article_type_mapping() -> dict[str, list[str]]:
    """Load the article type to method mapping from YAML.

    YAML is organized by method -> language -> patterns.
    We flatten all languages into a single list per method.
    """
    yaml_path = Path(__file__).parent.parent / 'data' / 'article_type_mapping.yaml'
    if not yaml_path.exists():
        return {}
    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    # Flatten: method -> [all patterns from all languages]
    result = {}
    for method, langs in data.items():
        patterns = []
        for lang, terms in langs.items():
            patterns.extend(terms)
        result[method] = patterns
    return result


def detect_method(article_type: str | None, mapping: dict[str, list[str]]) -> str | None:
    """Map article_type to canonical method value."""
    if not article_type:
        return None

    article_type_lower = article_type.lower()

    for method, patterns in mapping.items():
        for pattern in patterns:
            if pattern.lower() in article_type_lower:
                return method

    return None


def extract_doi(data: dict) -> str | None:
    """Extract DOI from article data or citation string."""
    # Check if DOI is directly in data
    if data.get('doi'):
        return data['doi']

    # Look for DOI pattern in citation
    citation = data.get('citation', '') or ''
    # DOI pattern: 10.XXXX/anything (until whitespace or end)
    doi_match = re.search(r'10\.\d{4,}/[^\s]+', citation)
    if doi_match:
        # Clean trailing punctuation
        doi = doi_match.group(0).rstrip('.,;:')
        return doi

    return None


def verify_peer_review_via_crossref(doi: str) -> tuple[bool | None, str]:
    """Query CrossRef API to verify peer review status.

    Returns:
        (True, reason) if confirmed peer-reviewed
        (False, reason) if confirmed NOT peer-reviewed
        (None, reason) if API failed or inconclusive
    """
    if not doi:
        return None, "No DOI available"

    # CrossRef API - free, no auth required
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"

    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'PDA-France/1.0 (mailto:contact@pda.expert)',
                'Accept': 'application/json'
            }
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))

        work = data.get('message', {})

        # Extract useful info
        container_title = work.get('container-title', [''])[0]
        work_type = work.get('type', '')
        issn = work.get('ISSN', [])

        # Strong indicators of peer review
        peer_review_types = ['journal-article', 'proceedings-article']

        if work_type in peer_review_types:
            reason = f"CrossRef: type='{work_type}'"
            if container_title:
                reason += f", journal='{container_title[:50]}'"
            if issn:
                reason += f", ISSN={issn[0]}"
            return True, reason

        # Non-peer-reviewed types
        non_peer_types = ['book-chapter', 'report', 'monograph', 'posted-content',
                          'dissertation', 'dataset', 'component']
        if work_type in non_peer_types:
            return False, f"CrossRef: type='{work_type}' (not peer-reviewed)"

        # Inconclusive
        return None, f"CrossRef: type='{work_type}' (uncertain)"

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, f"CrossRef: DOI not found ({doi})"
        return None, f"CrossRef API error: HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, f"CrossRef API error: {e.reason}"
    except Exception as e:
        return None, f"CrossRef API error: {e}"


def detect_peer_reviewed(data: dict) -> tuple[bool, str]:
    """Detect if article is peer reviewed based on DOI lookup and citation patterns.

    Priority:
    1. DOI verification via CrossRef (definitive if available)
    2. Signal-based heuristics (preponderance of evidence)
    """
    citation = data.get('citation', '') or ''
    references = data.get('references', [])

    # First, try DOI-based verification (definitive answer if available)
    doi = extract_doi(data)
    if doi:
        crossref_result, crossref_reason = verify_peer_review_via_crossref(doi)
        if crossref_result is not None:
            # CrossRef gave us a definitive answer
            return crossref_result, crossref_reason

    # Fall back to signal-based detection
    signals = []

    # Note if we tried DOI but it was inconclusive
    if doi:
        signals.append(f"DOI found ({doi}) but CrossRef inconclusive")

    # Check for journal indicators in citation
    journal_patterns = [
        r'\d+\(\d+\)',  # Volume(Issue) like 25(4)
        r'\d+:\d+',     # Volume:Pages like 88:595
        r'pp?\.\s*\d+', # Pages like p. 103
        r'\d+–\d+',     # Page range with en-dash
        r'\d+-\d+',     # Page range with hyphen
    ]

    for pattern in journal_patterns:
        if re.search(pattern, citation):
            signals.append(f"Journal format in citation: {pattern}")
            break

    # Check for DOI presence (even if CrossRef lookup failed)
    if doi or 'doi' in citation.lower():
        signals.append("DOI present")

    # Check references - peer-reviewed articles have many references with journal format
    if len(references) >= 10:
        signals.append(f"Has {len(references)} references")

    # Check if references look like journal citations (volume/issue patterns)
    journal_ref_count = 0
    for ref in references[:10]:  # Check first 10
        if re.search(r'\d{4};\d+', ref) or re.search(r'\d+\(\d+\):', ref):
            journal_ref_count += 1
    if journal_ref_count >= 5:
        signals.append(f"{journal_ref_count}/10 refs have journal format")

    # Check for common peer-reviewed journal name patterns in references
    journal_keywords = ['J ', 'Journal', 'Psychiatr', 'Psychol', 'Dev Disord',
                        'Arch ', 'Eur ', 'Child', 'Autism', 'Med ']
    journal_name_count = 0
    for ref in references[:10]:
        for kw in journal_keywords:
            if kw in ref:
                journal_name_count += 1
                break
    if journal_name_count >= 5:
        signals.append(f"{journal_name_count}/10 refs mention journals")

    # Make decision - need 2+ signals for confidence
    is_peer_reviewed = len(signals) >= 2
    reason = "; ".join(signals) if signals else "No peer review signals found"

    return is_peer_reviewed, reason


def detect_voice(data: dict, body_html: str = '') -> tuple[str | None, str]:
    """Detect voice classification from content signals."""
    # This is harder to automate - look for common patterns
    signals = []

    abstract = (data.get('abstract', '') or '').lower()
    acknowledgements = (data.get('acknowledgements', '') or '').lower()
    body_lower = body_html.lower()

    # Academic signals
    academic_terms = ['university', 'université', 'department', 'département',
                      'research', 'recherche', 'study', 'étude', 'professor']
    for term in academic_terms:
        if term in abstract or term in acknowledgements:
            signals.append(('academic', f"Found '{term}'"))
            break

    # Practitioner signals
    practitioner_terms = ['clinical', 'clinique', 'hospital', 'hôpital',
                          'patient', 'practice', 'pratique', 'therapeutic',
                          'thérapeutique', 'diagnostic']
    for term in practitioner_terms:
        if term in abstract or term in body_lower[:2000]:
            signals.append(('practitioner', f"Found '{term}'"))
            break

    # Organization signals
    org_terms = ['pda society', 'cerebra', 'autism society', 'charity',
                 'association', 'foundation', 'fondation']
    for term in org_terms:
        if term in abstract or term in acknowledgements:
            signals.append(('organization', f"Found '{term}'"))
            break

    # Individual signals
    individual_terms = ['my child', 'mon enfant', 'our family', 'notre famille',
                        'as a parent', 'en tant que parent', 'my experience']
    for term in individual_terms:
        if term in abstract or term in body_lower[:2000]:
            signals.append(('individual', f"Found '{term}'"))
            break

    if not signals:
        return None, "No voice signals detected"

    # Return the most likely (first found)
    voice, reason = signals[0]
    return voice, reason


def extract_year_from_citation(citation: str | None) -> str | None:
    """Extract publication year from citation string."""
    if not citation:
        return None

    # Look for 4-digit year (19xx or 20xx)
    match = re.search(r'\b(19|20)\d{2}\b', citation)
    return match.group(0) if match else None


def main():
    parser = argparse.ArgumentParser(description='Enhance parsed article with AI-extracted metadata')
    parser.add_argument('json_path', type=Path, help='Path to the parsed JSON file')
    parser.add_argument('--suggest', action='store_true', help='Print suggestions without modifying')
    parser.add_argument('--apply', action='store_true', help='Apply suggested values to JSON')

    # Manual field overrides (Claude extracts from reading raw HTML)
    parser.add_argument('--authors', type=str, help='Override: Authors string')
    parser.add_argument('--year', type=str, help='Override: Publication year')
    parser.add_argument('--citation', type=str, help='Override: Full citation')
    parser.add_argument('--title', type=str, help='Override: Article title')
    parser.add_argument('--abstract', type=str, help='Override: Abstract text')
    parser.add_argument('--acknowledgements', type=str, help='Override: Acknowledgements')
    parser.add_argument('--keywords', type=str, help='Override: Keywords')

    # Classification overrides
    parser.add_argument('--method', type=str,
                        choices=['empirical', 'synthesis', 'theoretical', 'lived_experience'],
                        help='Override: Method classification')
    parser.add_argument('--voice', type=str,
                        choices=['academic', 'practitioner', 'organization', 'individual'],
                        help='Override: Voice classification')
    parser.add_argument('--peer-reviewed', action='store_true', help='Override: Mark as peer reviewed')
    parser.add_argument('--not-peer-reviewed', action='store_true', help='Override: Mark as NOT peer reviewed')

    args = parser.parse_args()

    if not args.json_path.exists():
        print(f"Error: JSON file not found: {args.json_path}", file=sys.stderr)
        sys.exit(1)

    data = load_json(args.json_path)
    mapping = load_article_type_mapping()

    # Detect values
    suggestions = {}

    # Method from article_type
    article_type = data.get('article_type')
    detected_method = detect_method(article_type, mapping)
    if detected_method:
        suggestions['method'] = {
            'value': detected_method,
            'reason': f"Mapped from article_type: '{article_type}'"
        }

    # Peer reviewed
    is_peer_reviewed, pr_reason = detect_peer_reviewed(data)
    suggestions['peer_reviewed'] = {
        'value': is_peer_reviewed,
        'reason': pr_reason
    }

    # Voice (harder to detect)
    detected_voice, voice_reason = detect_voice(data)
    if detected_voice:
        suggestions['voice'] = {
            'value': detected_voice,
            'reason': voice_reason
        }

    # Year from citation
    if not data.get('year'):
        detected_year = extract_year_from_citation(data.get('citation'))
        if detected_year:
            suggestions['year'] = {
                'value': detected_year,
                'reason': f"Extracted from citation"
            }

    # Print suggestions
    if args.suggest or (not args.apply and not any([args.authors, args.year, args.citation, args.method, args.voice])):
        print(f"\n=== Enhancement Suggestions for {args.json_path.name} ===\n")

        print(f"Article Type (from parser): {article_type or '(not detected)'}")
        print()

        for field, info in suggestions.items():
            current = data.get(field)
            print(f"{field}:")
            print(f"  Current: {current}")
            print(f"  Suggested: {info['value']}")
            print(f"  Reason: {info['reason']}")
            print()

        # Show what's missing
        missing = []
        if not data.get('authors'):
            missing.append('authors')
        if not data.get('citation'):
            missing.append('citation')
        if not data.get('abstract'):
            missing.append('abstract')

        if missing:
            print(f"Still missing (need manual extraction): {', '.join(missing)}")

        if not args.apply:
            print("\nRun with --apply to write suggestions to JSON")
            return

    # Apply changes
    changes = []

    # Apply suggestions if --apply
    if args.apply:
        for field, info in suggestions.items():
            if data.get(field) is None or data.get(field) == '':
                data[field] = info['value']
                changes.append(f"{field}: {info['value']} ({info['reason']})")

    # Apply manual overrides (these always take precedence)
    if args.authors:
        data['authors'] = args.authors
        changes.append(f"authors: {args.authors}")

    if args.year:
        data['year'] = args.year
        changes.append(f"year: {args.year}")

    if args.citation:
        data['citation'] = args.citation
        changes.append(f"citation: {args.citation[:50]}...")

    if args.title:
        data['title'] = args.title
        changes.append(f"title: {args.title[:50]}...")

    if args.abstract:
        data['abstract'] = args.abstract
        changes.append(f"abstract: {args.abstract[:50]}...")

    if args.acknowledgements:
        data['acknowledgements'] = args.acknowledgements
        changes.append(f"acknowledgements: {args.acknowledgements[:50]}...")

    if args.keywords:
        data['keywords'] = args.keywords
        changes.append(f"keywords: {args.keywords[:50]}...")

    if args.method:
        data['method'] = args.method
        changes.append(f"method: {args.method}")

    if args.voice:
        data['voice'] = args.voice
        changes.append(f"voice: {args.voice}")

    if args.peer_reviewed:
        data['peer_reviewed'] = True
        changes.append("peer_reviewed: True")

    if args.not_peer_reviewed:
        data['peer_reviewed'] = False
        changes.append("peer_reviewed: False")

    if changes:
        save_json(args.json_path, data)
        print(f"Updated {args.json_path.name}:")
        for change in changes:
            print(f"  - {change}")
    else:
        print("No changes to make")


if __name__ == '__main__':
    main()
