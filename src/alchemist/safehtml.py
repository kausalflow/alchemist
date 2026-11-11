"""Markdown from the model, as HTML that is safe to put in a page.

A conclusion is model text, and the model reads documents and data values that anyone may have written: injected text
could make it end its conclusion with a `<script>` or an `onerror=` handler. `markdown.markdown()` passes raw HTML
through, so its output is cleaned with nh3 (an allow-list HTML sanitiser): formatting, tables and links stay; scripts,
event handlers, styles, forms, frames, images (a way to send data to another site) and `javascript:` links go.
"""

from __future__ import annotations

import markdown
import nh3

TAGS = {"p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em", "i", "code", "pre", "blockquote",
        "ul", "ol", "li", "a", "table", "thead", "tbody", "tr", "th", "td", "sup", "sub", "del", "span"}
ATTRIBUTES = {"a": {"href", "title"}, "th": {"align"}, "td": {"align"},
              "ol": {"start"}, "code": {"class"}}
URL_SCHEMES = {"http", "https", "mailto"}


def clean(html: str) -> str:
    """Keep only the allowed tags and attributes; links open as plain links without a referrer."""
    return nh3.clean(html, tags=TAGS, attributes=ATTRIBUTES, url_schemes=URL_SCHEMES, link_rel="noopener noreferrer")


def markdown_html(text: str, extensions: list[str] | None = None) -> str:
    """Markdown to sanitised HTML."""
    return clean(markdown.markdown(text, extensions=extensions or []))
