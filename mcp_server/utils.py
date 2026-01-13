"""
Shared utilities for the MCP server.
"""

import re


def slugify(text: str) -> str:
    """
    Convert text to a clean URL-safe slug.

    - Lowercase
    - Replace spaces/underscores with hyphens
    - Remove non-alphanumeric characters (except hyphens)
    - Collapse multiple hyphens
    - Strip leading/trailing hyphens

    Examples:
        "Hello World" -> "hello-world"
        "O'Nions et al. 2014" -> "onions-et-al-2014"
        "Le syndrome d'évitement" -> "le-syndrome-dvitement"
    """
    text = text.lower()
    # Replace spaces and underscores with hyphens
    text = re.sub(r'[\s_]+', '-', text)
    # Remove anything that isn't alphanumeric or hyphen
    text = re.sub(r'[^a-z0-9-]', '', text)
    # Collapse multiple hyphens
    text = re.sub(r'-+', '-', text)
    # Strip leading/trailing hyphens
    text = text.strip('-')
    return text
