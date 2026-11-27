"""콘솔 없는 게임 실행 진입점. 치명적 오류만 파일에 남긴다."""

import ctypes
from datetime import datetime
import os
from pathlib import Path
import sys
import traceback


def main():
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RevenantLost" / "logs"
    path = root / "startup.log"
    log_available = True
    try:
        root.mkdir(parents=True, exist_ok=True)
        output = path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        log_available = False
        output = open(os.devnull, "w", encoding="utf-8")
    previous_output, previous_error = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = output
    try:
        from .__main__ import main as run_game

        run_game([])
    except Exception:
        message = traceback.format_exc()
        try:
            output.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n{message}")
            detail = (f"게임 실행 중 오류가 발생했습니다.\n로그: {path}" if log_available
                      else f"게임 실행 중 오류가 발생했습니다.\n\n{message[-1200:]}")
        except OSError:
            detail = f"게임 실행 중 오류가 발생했습니다.\n\n{message[-1200:]}"
        ctypes.windll.user32.MessageBoxW(None, detail, "REVENANT : L.O.S.T", 0x10)
        raise SystemExit(1)
    finally:
        sys.stdout, sys.stderr = previous_output, previous_error
        output.close()


if __name__ == "__main__":
    main()
