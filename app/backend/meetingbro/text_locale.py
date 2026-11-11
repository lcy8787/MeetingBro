"""Use macOS's built-in ICU transliterator; no model or package downloads."""
from __future__ import annotations

import ctypes
from functools import lru_cache
import sys


@lru_cache(maxsize=1)
def _core_foundation():
    if sys.platform != "darwin":
        return None
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    pointer = ctypes.c_void_p
    cf.CFStringCreateMutable.argtypes = [pointer, ctypes.c_long]
    cf.CFStringCreateMutable.restype = pointer
    cf.CFStringAppendCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = pointer
    cf.CFStringTransform.argtypes = [pointer, pointer, pointer, ctypes.c_bool]
    cf.CFStringTransform.restype = ctypes.c_bool
    cf.CFStringGetLength.argtypes = [pointer]
    cf.CFStringGetLength.restype = ctypes.c_long
    cf.CFStringGetCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [pointer]
    return cf


def to_traditional(text: str) -> str:
    """Convert Chinese script on macOS, preserving other languages verbatim."""
    if not text or not any("\u3400" <= char <= "\u9fff" for char in text):
        return text
    cf = _core_foundation()
    if cf is None:
        return text
    utf8 = 0x08000100
    value = cf.CFStringCreateMutable(None, 0)
    transform = cf.CFStringCreateWithCString(None, b"Hans-Hant", utf8)
    try:
        cf.CFStringAppendCString(value, text.encode("utf-8"), utf8)
        if not cf.CFStringTransform(value, None, transform, False):
            return text
        buffer = ctypes.create_string_buffer(cf.CFStringGetLength(value) * 4 + 1)
        if cf.CFStringGetCString(value, buffer, len(buffer), utf8):
            return buffer.value.decode("utf-8")
        return text
    finally:
        cf.CFRelease(value)
        cf.CFRelease(transform)
