"""Terminate the owned server when its backend closes the parent pipe."""
import signal
import subprocess
import sys
import threading


def main():
    child = subprocess.Popen(sys.argv[1:])

    def stop(*_):
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()

    def watch_parent():
        sys.stdin.buffer.read()
        stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        return child.wait()
    finally:
        stop()


if __name__ == "__main__":
    sys.exit(main())
