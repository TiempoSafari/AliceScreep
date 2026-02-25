from __future__ import annotations

try:
    from opencc import OpenCC
except Exception:
    OpenCC = None


class OpenCCConverter:
    def __init__(self) -> None:
        self._converter = None
        if OpenCC is not None:
            try:
                self._converter = OpenCC("t2s")
            except Exception:
                self._converter = None

    @property
    def available(self) -> bool:
        return self._converter is not None

    def convert(self, text: str) -> str:
        if not self._converter:
            return text
        return self._converter.convert(text)


OPENCC = OpenCCConverter()


def maybe_convert_to_simplified(text: str, enabled: bool) -> str:
    if not enabled:
        return text
    return OPENCC.convert(text)
