from __future__ import annotations

from pathlib import Path

from main import data_file, main as run_application


def easy_data_file() -> Path:
    return data_file()


def main() -> int:
    return run_application(data_path=easy_data_file(), easy_mode=True)


if __name__ == "__main__":
    raise SystemExit(main())
