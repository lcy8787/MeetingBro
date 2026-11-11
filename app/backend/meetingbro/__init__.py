"""MeetingBro runtime defaults, set before optional inference libraries load."""
import os

# ONNX Runtime 1DS can hang at process teardown while flushing its uploader.
# This app's audio pipeline must work entirely offline and quit promptly.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
