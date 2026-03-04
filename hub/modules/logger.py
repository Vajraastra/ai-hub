"""
AI Hub - Logger (Bitácora)
Centralized logging for all hub operations.
Writes timestamped entries to bitacora.log and optionally to stdout.
Zero external dependencies - stdlib only.
"""

import os
import datetime


class HubLogger:
    """Simple file + stdout logger for tracking hub operations."""

    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.log_file = os.path.join(log_dir, "bitacora.log")

    def _timestamp(self) -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _write(self, level: str, category: str, message: str):
        entry = f"[{self._timestamp()}] [{level}] [{category}] {message}"
        # Write to file
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except IOError:
            pass
        # Also print to stdout
        print(entry)

    def info(self, category: str, message: str):
        self._write("INFO", category, message)

    def warn(self, category: str, message: str):
        self._write("WARN", category, message)

    def error(self, category: str, message: str):
        self._write("ERROR", category, message)

    def success(self, category: str, message: str):
        self._write("OK", category, message)

    def section(self, title: str):
        """Log a visual section separator."""
        separator = "=" * 50
        self._write("INFO", "---", separator)
        self._write("INFO", "---", title)
        self._write("INFO", "---", separator)


# === Self-test ===
if __name__ == "__main__":
    import tempfile
    test_dir = tempfile.mkdtemp()
    log = HubLogger(test_dir)
    log.section("Logger Self-Test")
    log.info("test", "This is an info message")
    log.warn("test", "This is a warning")
    log.error("test", "This is an error")
    log.success("test", "This is a success")
    print(f"\nLog written to: {log.log_file}")
