"""
XSS Sanitization utilities
"""
import bleach
from typing import Optional


class XSSSanitizer:
    """Utility class for sanitizing user inputs to prevent XSS attacks"""

    SAFE_TEXT_TAGS = [
        "p",
        "br",
        "strong",
        "em",
        "u",
        "ol",
        "ul",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    ]
    SAFE_HTML_TAGS = SAFE_TEXT_TAGS + [
        "a",
        "blockquote",
        "div",
        "span",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "img",
    ]
    SAFE_HTML_ATTRIBUTES = {
        "*": ["class", "id", "title"],
        "a": ["href", "name", "rel", "target", "title"],
        "img": ["alt", "height", "src", "width"],
        "table": ["border", "cellpadding", "cellspacing", "height", "width"],
        "td": ["align", "colspan", "height", "rowspan", "valign", "width"],
        "th": ["align", "colspan", "height", "rowspan", "valign", "width"],
    }
    SAFE_PROTOCOLS = ["http", "https", "mailto", "cid"]

    @classmethod
    def sanitize_text(
        cls,
        text: Optional[str],
        strip_html: bool = True,
        allow_safe_css: bool = True,
    ) -> Optional[str]:
        """
        Sanitize text input to remove XSS content.

        Args:
            text: Input text to sanitize
            strip_html: Whether to strip all HTML tags
            allow_safe_css: Retained for backward compatibility; inline CSS is stripped.

        Returns:
            Cleaned text or None if input was None
        """
        if text is None:
            return None

        if not isinstance(text, str):
            text = str(text)

        if strip_html:
            text = bleach.clean(
                text,
                tags=[],
                attributes={},
                protocols=[],
                strip=True,
            )
        else:
            safe_attributes = {"*": ["class", "id"]}
            text = bleach.clean(
                text,
                tags=cls.SAFE_TEXT_TAGS,
                attributes=safe_attributes,
                protocols=cls.SAFE_PROTOCOLS,
                strip=True,
            )

        return text.strip()

    @classmethod
    def sanitize_html(cls, html: Optional[str]) -> Optional[str]:
        """
        Sanitize HTML content, removing dangerous scripts and event handlers
        while preserving safe structural HTML.

        Args:
            html: HTML content to sanitize

        Returns:
            Sanitized HTML with dangerous content removed
        """
        if html is None:
            return None

        if not isinstance(html, str):
            html = str(html)

        return bleach.clean(
            html,
            tags=cls.SAFE_HTML_TAGS,
            attributes=cls.SAFE_HTML_ATTRIBUTES,
            protocols=cls.SAFE_PROTOCOLS,
            strip=True,
        )


# Convenience functions
def sanitize_text(text: Optional[str], strip_html: bool = True) -> Optional[str]:
    """Sanitize text input to remove XSS content"""
    return XSSSanitizer.sanitize_text(text, strip_html)


def sanitize_html(html: Optional[str]) -> Optional[str]:
    """Sanitize HTML content, removing dangerous scripts and event handlers"""
    return XSSSanitizer.sanitize_html(html)
