"""Run the game under the external py-spy sampler and summarize sampled stacks."""

import argparse
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
from time import process_time

from .profile_report import flamegraph_svg, interactive_html, markdown_report


ROOT = Path(__file__).resolve().parents[1]


def summarize_speedscope(document):
    """Return per-function self and inclusive active-sample times in seconds."""
    frames = document["shared"]["frames"]
    totals = {}
    for profile in document["profiles"]:
        if profile["type"] != "sampled" or profile["unit"] != "seconds":
            raise ValueError("초 단위 speedscope sampled 프로파일이 필요합니다")
        for stack, weight in zip(profile["samples"], profile["weights"], strict=True):
            if not stack or weight <= 0:
                continue
            keys = []
            for index in stack:
                frame = frames[index]
                if not frame.get("file") and frame["name"].startswith("process "):
                    continue
                keys.append((frame.get("file") or "", frame["name"], frame.get("line") or 0))
            if not keys:
                continue
            for key in set(keys):
                row = totals.setdefault(key, [0.0, 0.0, 0, 0])
                row[1] += weight
                row[3] += 1
            row = totals[keys[-1]]
            row[0] += weight
            row[2] += 1
    return sorted(
        ((key, values) for key, values in totals.items()),
        key=lambda item: (-item[1][0], -item[1][1], item[0]),
    )


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(("file", "function", "line", "self_cpu_s_est", "inclusive_cpu_s_est",
                         "self_samples", "inclusive_samples"))
        for (file, name, line), (self_seconds, inclusive_seconds, self_count, inclusive_count) in rows:
            writer.writerow((file, name, line, f"{self_seconds:.6f}", f"{inclusive_seconds:.6f}",
                             self_count, inclusive_count))


def game_python_environment():
    """Use the real Windows interpreter, which py-spy can inspect, with venv packages."""
    env = os.environ.copy()
    executable = sys.executable
    if os.name == "nt" and sys.prefix != sys.base_prefix:
        executable = getattr(sys, "_base_executable", executable)
        packages = sysconfig.get_path("purelib")
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (packages, env.get("PYTHONPATH"))))
        env["PYTHONNOUSERSITE"] = "1"
    return executable, env


def sampler_cpu_seconds(process):
    """Read only py-spy's CPU time, separately from the sampled game on Windows."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    created = wintypes.FILETIME()
    exited = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    get_times = ctypes.windll.kernel32.GetProcessTimes
    if not get_times(int(process._handle), ctypes.byref(created), ctypes.byref(exited),
                     ctypes.byref(kernel), ctypes.byref(user)):
        return None
    ticks = ((kernel.dwHighDateTime << 32) | kernel.dwLowDateTime)
    ticks += (user.dwHighDateTime << 32) | user.dwLowDateTime
    return ticks / 10_000_000


def main(argv=None):
    parser = argparse.ArgumentParser(description="py-spy로 게임의 함수별 CPU 활성 시간을 표본 추정")
    parser.add_argument("--output", "--reports-dir", dest="reports_dir", type=Path,
                        default=ROOT / "reports", help="타임스탬프 폴더를 만들 상위 경로 (기본 reports)")
    parser.add_argument("--rate", type=int, default=100, help="초당 표본 수 (기본 100)")
    parser.add_argument("game_args", nargs=argparse.REMAINDER, help="-- 뒤에 prototype 실행 인수")
    args = parser.parse_args(argv)
    if not 1 <= args.rate <= 1000:
        parser.error("표본 수는 초당 1~1000이어야 합니다")
    game_args = args.game_args[1:] if args.game_args[:1] == ["--"] else args.game_args
    spy = Path(sys.executable).with_name("py-spy.exe" if os.name == "nt" else "py-spy")
    if not spy.is_file():
        found = shutil.which("py-spy")
        if not found:
            parser.error("py-spy가 없습니다. SetupEnvironment.bat로 의존성을 설치하세요")
        spy = Path(found)
    started_at = datetime.now().astimezone()
    report_dir = args.reports_dir.resolve() / started_at.strftime("%Y-%m-%d_%H-%M-%S-%f")
    report_dir.mkdir(parents=True)
    speedscope = report_dir / "samples.speedscope.json"
    csv_path = report_dir / "functions.csv"
    markdown = report_dir / "report.md"
    svg_path = report_dir / "flamegraph.svg"
    html_path = report_dir / "flamegraph.html"
    metadata_path = report_dir / "metadata.json"
    python, env = game_python_environment()
    command = [str(spy), "record", "--format", "speedscope", "--rate", str(args.rate),
               "--function", "--full-filenames", "--subprocesses", "--output", str(speedscope),
               "--", python, "-B", "-u", "-X", "utf8", "-m", "prototype", *game_args]
    print(f"프로파일 수집 중: {speedscope}", flush=True)
    reporter_started = process_time()
    try:
        with subprocess.Popen(command, cwd=ROOT, env=env) as process:
            returncode = process.wait()
            sampler_cpu = sampler_cpu_seconds(process)
    except OSError as error:
        parser.exit(1, f"py-spy 실행 실패: {error}\n")
    if returncode:
        parser.exit(returncode, "py-spy 수집 실패: 보고서를 생성하지 않았습니다.\n")
    try:
        document = json.loads(speedscope.read_text(encoding="utf-8"))
        rows = summarize_speedscope(document)
        write_csv(csv_path, rows)
        metadata = {
            "sample_rate_hz": args.rate,
            "sampled_active_thread_s": sum(sum(profile["weights"]) for profile in document["profiles"]),
            "sampler_cpu_s": sampler_cpu,
            "reporter_cpu_s": process_time() - reporter_started,
            "note": "Sampler and reporter CPU are outside the sampled game process; function times are sampling estimates.",
        }
        svg = flamegraph_svg(document)
        svg_path.write_text(svg, encoding="utf-8")
        html_path.write_text(interactive_html(svg), encoding="utf-8")
        metadata["reporter_cpu_s"] = process_time() - reporter_started
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        markdown.write_text(markdown_report(rows, metadata, started_at, game_args), encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"프로파일 분석 실패: {error}\n")
    print(f"보고서: {markdown} ({len(rows)}개 함수)")
    print(f"대화형 플레임 그래프: {html_path}")
    print(f"SVG 플레임 그래프: {svg_path}")
    print(f"함수별 CSV: {csv_path}")
    print(f"계측 프로세스 CPU: {sampler_cpu:.3f}s (게임 함수 시간과 분리)" if sampler_cpu is not None
          else "계측 프로세스 CPU: 이 플랫폼에서 별도 측정 불가")
    print(f"계측 메타데이터: {metadata_path}")
    print("self CPU 추정 상위 함수 (초):")
    for (file, name, line), (seconds, _, _, _) in rows[:15]:
        print(f"  {seconds:8.3f}  {name}  {file}:{line}")
    print("표본 추정치입니다. 짧은 호출은 누락될 수 있고 inclusive 값은 함수 간 합산할 수 없습니다.")


if __name__ == "__main__":
    main()
