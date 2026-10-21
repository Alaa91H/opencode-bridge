MAX_MESSAGE_LENGTH = 4096


def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _prefix_within_utf16_limit(s: str, limit: int) -> str:
    if _utf16_len(s) <= limit:
        return s
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _utf16_len(s[:mid]) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo]


def chunk_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    if _utf16_len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if _utf16_len(remaining) <= max_len:
            chunks.append(remaining)
            break

        chunk = _prefix_within_utf16_limit(remaining, max_len)

        paragraph_break = chunk.rfind("\n\n")
        single_break = chunk.rfind("\n")

        if paragraph_break > max_len * 0.5:
            split_at = paragraph_break
        elif single_break > max_len * 0.7:
            split_at = single_break
        else:
            split_at = chunk.rfind(" ")
            if split_at < max_len * 0.3:
                split_at = _prefix_within_utf16_limit(
                    remaining, max_len - 10
                ).rfind(" ")
                if split_at < 1:
                    split_at = max_len - 10

        if split_at < 1:
            split_at = max_len - 10

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    return [c for c in chunks if c]


def format_and_chunk(text: str) -> list[str]:
    return chunk_message(text)
