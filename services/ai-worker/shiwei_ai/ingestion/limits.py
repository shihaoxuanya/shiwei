"""Shared limits for local file imports (web response limits are separate)."""

MAX_FILE_BYTES = 1024 ** 3
MAX_PDF_PAGES = 3000


def validate_file_size(size: int) -> None:
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError("无法读取有效的文件大小，请检查文件后重试")
    if size > MAX_FILE_BYTES:
        raise ValueError("文件超过 1GB（1024MB），请拆分或压缩后添加")
