"""Presentation-only range normalization; never used to rewrite stored sources."""
import re

# Leave code, URLs, Markdown link destinations and HTML literal spans untouched.
PROTECTED = re.compile(r"(```[\s\S]*?(?:```|\Z)|~~~[\s\S]*?(?:~~~|\Z)|`+[^`\n]*`+|^(?: {4}|\t)[^\n]*|https?://[^\s]+|!?\[[^\]\n]*\]\([^\)\n]*\)|<[^>\n]+>)", re.M)
RANGE = re.compile(
    r"(?<![A-Za-z0-9_.~～])(?P<low>[+-]?\d+(?:\.\d+)?)[ \t]*(?:\\?~|～)[ \t]*"
    r"(?P<high>[+-]?\d+(?:\.\d+)?)(?![\d.~～])"
    r"(?P<unit>[ \t]*(?:天|小时|分钟|秒|周|个月|年|GB|MB|TB|KB|%))?", re.I,
)


def normalize_numeric_ranges(text: str) -> str:
    def replace(match):
        unit = (match["unit"] or "").strip()
        separator = " " if unit and re.search(r"[\u4e00-\u9fff]", unit) else ""
        return f'{match["low"]}～{match["high"]}{separator}{unit}'

    return "".join(part if index % 2 else RANGE.sub(replace, part)
                   for index, part in enumerate(PROTECTED.split(text)))
