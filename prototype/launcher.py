"""장면·생성 옵션·자동 검증을 선택하는 통합 실행 창."""

import json
import math
from pathlib import Path
from queue import Empty, Queue
import random
import subprocess
import sys
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .ai_scenarios import SCENARIOS
from .world_layout import MAX_REGIONS


ROOT = Path(__file__).resolve().parents[1]
REGIONS = {"소형": "small", "중형": "medium", "대형": "large", "초대형": "xlarge"}
IMPORTANCE = {"일반": "normal", "중요": "important", "핵심": "core"}
SWITCHES = {"장면 기본값": None, "켜기": True, "끄기": False}


def discover_targets():
    targets = {"generated": {"name": "절차 생성 맵", "kind": "generated", "modes": {"기본": ()}},
               "continue": {"name": "생성 월드 이어하기", "kind": "resume", "modes": {"기본": ()}},
               "equipment-range": {"name": "장비 시험장", "kind": "play", "modes": {"기본": ("--equipment-range",)}},
               "ai": {"name": "창고 전술 AI", "kind": "play",
                      "modes": {row[0]: ("--ai-scenario", key) for key, row in SCENARIOS.items()}}}
    for path in sorted((ROOT / "prototype/scenarios").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        modes = {"기본": ()}
        if "search_drill" in data:
            modes["실내 수색"] = ("--search-drill",)
        for index, (left, right) in enumerate(data.get("stack_drill", {}).get("layouts", ())):
            modes[f"문 집결 {index + 1} · {left}+{right}명"] = ("--stack-variant", str(index))
        targets[path.stem] = {"name": data.get("name", path.stem), "kind": "play", "path": path, "modes": modes}
    targets["all-tests"] = {"name": "전체 자동 검증", "kind": "test", "module": None}
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        targets["test/" + path.stem] = {"name": path.stem.removeprefix("test_"), "kind": "test",
                                      "module": "tests." + path.stem}
    return targets


def build_command(target, values):
    executable = str(Path(sys.executable).with_name("python.exe")) if sys.executable.endswith("pythonw.exe") else sys.executable
    command = [executable, "-B", "-u", "-X", "utf8", "-m"]
    if target["kind"] == "test":
        return command + ["unittest", target["module"], "-v"] if target["module"] else command + ["unittest", "discover", "-s", "tests", "-v"]
    command += ["prototype"]
    if target["kind"] == "resume":
        command += ["--load"]
    if "path" in target:
        command += ["--scene", str(target["path"])]
    if target["kind"] == "generated":
        try:
            seed = int(values["seed"])
        except ValueError:
            raise ValueError("시드는 정수로 입력하세요") from None
        try:
            regions = int(values.get("regions", "1"))
        except (TypeError, ValueError):
            raise ValueError(f"지역 수는 1~{MAX_REGIONS} 정수로 입력하세요") from None
        if not 1 <= regions <= MAX_REGIONS:
            raise ValueError(f"지역 수는 1~{MAX_REGIONS} 정수로 입력하세요")
        command += ["--generate", str(seed), "--region-size", REGIONS[values["region"]],
                    "--importance", IMPORTANCE[values["importance"]], "--regions", str(regions)]
        for key, mapping in (("region_sizes", REGIONS), ("region_importances", IMPORTANCE)):
            if values.get(key, '').strip():
                items = [mapping.get(item.strip(), item.strip()) for item in values[key].split(',')]
                if len(items) != regions or any(item not in mapping.values() for item in items):
                    raise ValueError("지역별 설정은 지역 수만큼 쉼표로 구분해 입력하세요")
                command += ['--' + key.replace('_', '-'), ','.join(items)]
    command += list(target["modes"][values["mode"]])
    try:
        frames = int(values["frames"])
    except ValueError:
        raise ValueError("프레임 수는 0 이상의 정수로 입력하세요") from None
    if frames < 0:
        raise ValueError("프레임 수는 0 이상이어야 합니다")
    command += ["--frames", str(frames)]
    if values["headless"]:
        return profile_command(command + ["--headless"], values)
    if values["zoom"] != "장면 기본값":
        try:
            zoom = float(values["zoom"])
        except ValueError:
            raise ValueError("줌은 숫자 또는 장면 기본값을 선택하세요") from None
        if not math.isfinite(zoom) or not 0.5 <= zoom <= 4:
            raise ValueError("줌은 0.5~4.0 사이로 입력하세요")
        command += ["--zoom", str(zoom)]
    for key in ("paused", "debug"):
        if SWITCHES[values[key]] is not None:
            command.append("--" + ("" if SWITCHES[values[key]] else "no-") + key)
    if values["map_open"]:
        command.append("--map-open")
    return profile_command(command, values)


def profile_command(command, values):
    if not values.get("profile", False):
        return command
    return command[:6] + ["prototype.profiler", "--", *command[7:]]


class Launcher:
    def __init__(self, root):
        self.root, self.targets = root, discover_targets()
        self.process, self.output = None, Queue()
        self.profiled_process = False
        self.selected_key, self.last_modes = None, {}
        root.title("REVENANT · 통합 테스트 런처")
        root.geometry("980x820")
        root.minsize(840, 760)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=0)
        root.rowconfigure(1, weight=1)
        body = ttk.Frame(root, padding=12)
        body.grid(sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        tree_frame = ttk.LabelFrame(body, text="실행할 테스트", padding=6)
        tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse", height=16)
        self.tree.column("#0", width=350, minwidth=240)
        scroll = ttk.Scrollbar(tree_frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.insert("", "end", iid="play-group", text="플레이 테스트", open=True)
        self.tree.insert("", "end", iid="test-group", text="자동 검증", open=False)
        for key, target in self.targets.items():
            parent = "test-group" if target["kind"] == "test" else "play-group"
            self.tree.insert(parent, "end", iid=key, text=target["name"])
        options = ttk.LabelFrame(body, text="실행 옵션", padding=12)
        options.grid(row=0, column=1, sticky="nsew")
        options.columnconfigure(1, weight=1)
        defaults = {"mode": "기본", "seed": "42", "region": "소형", "regions": "1", "importance": "일반", "frames": "0",
                    "zoom": "장면 기본값", "paused": "장면 기본값", "debug": "장면 기본값",
                    "region_sizes": "", "region_importances": ""}
        self.values = {key: tk.StringVar(root, value=value) for key, value in defaults.items()}
        self.values.update({key: tk.BooleanVar(root) for key in ("headless", "map_open", "profile")})
        self.fields = {}
        rows = [("mode", "훈련 종류", ["기본"]), ("seed", "생성 시드", None),
                ("region", "지역 크기", list(REGIONS)), ("importance", "지역 중요도", list(IMPORTANCE)),
                ("regions", "지역 수", [str(count) for count in range(1, MAX_REGIONS + 1)]),
                ("region_sizes", "지역별 크기 (선택)", None), ("region_importances", "지역별 중요도 (선택)", None),
                ("zoom", "시작 줌", ["장면 기본값", "0.5", "0.55", "1.0", "1.5", "2.0", "4.0"]),
                ("paused", "시작 일시정지", list(SWITCHES)), ("debug", "개발 정보", list(SWITCHES)),
                ("frames", "프레임 제한", None)]
        for index, (key, label, choices) in enumerate(rows):
            ttk.Label(options, text=label).grid(row=index, column=0, sticky="w", pady=5, padx=(0, 12))
            widget = (ttk.Entry(options, textvariable=self.values[key]) if choices is None else
                      ttk.Combobox(options, textvariable=self.values[key], values=choices,
                                   state="normal" if key == "zoom" else "readonly"))
            widget.grid(row=index, column=1, sticky="ew", pady=5)
            self.fields[key] = widget
        self.random_button = ttk.Button(options, text="새 시드", command=lambda: self.values["seed"].set(random.SystemRandom().randrange(2**31)))
        self.random_button.grid(row=1, column=2, padx=(6, 0))
        for row, (key, label) in enumerate((("headless", "화면 없이 코어만 실행"),
                                             ("map_open", "지도를 연 상태로 시작"),
                                             ("profile", "함수별 CPU 시간 기록 (py-spy)")), len(rows)):
            widget = ttk.Checkbutton(options, text=label, variable=self.values[key])
            widget.grid(row=row, column=0, columnspan=3, sticky="w", pady=4)
            self.fields[key] = widget
        ttk.Label(options, text="지역별 설정: 비우면 공통값 사용 · 쉼표로 구분\n예: 소형,중형,소형,소형 / 일반,핵심,일반,일반\n프레임 0: 무제한 · P 일시정지 · M 지도 · F5/F9 저장·불러오기",
                  foreground="#555555", wraplength=410).grid(row=len(rows) + 3, column=0, columnspan=3, sticky="w", pady=(12, 0))
        log_frame = ttk.LabelFrame(root, text="실행 결과 · 오류", padding=6)
        log_frame.grid(row=1, column=0, sticky="nsew", padx=12)
        self.log = ScrolledText(log_frame, height=10, wrap="word", state="disabled", font=("Consolas", 10))
        self.log.pack(fill="both", expand=True)
        footer = ttk.Frame(root, padding=12)
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.status = tk.StringVar(root, value="테스트를 선택하세요")
        ttk.Label(footer, textvariable=self.status).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="로그 지우기", command=self.clear_log).grid(row=0, column=1, padx=4)
        self.stop_button = ttk.Button(footer, text="실행 종료", command=self.stop, state="disabled")
        self.stop_button.grid(row=0, column=2, padx=4)
        self.start_button = ttk.Button(footer, text="실행", command=self.start)
        self.start_button.grid(row=0, column=3, padx=4)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.values["headless"].trace_add("write", lambda *_: self.select())
        self.tree.selection_set("generated")
        self.select()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def select(self, event=None):
        selection = self.tree.selection()
        target = self.targets.get(selection[0]) if selection else None
        play = target is not None and target["kind"] != "test"
        generated = play and target["kind"] == "generated"
        modes = target.get("modes", {"해당 없음": ()}) if target else {"해당 없음": ()}
        self.fields["mode"].configure(values=list(modes))
        key = selection[0] if selection else None
        if key != self.selected_key:
            self.last_modes[self.selected_key] = self.values["mode"].get()
            self.values["mode"].set(self.last_modes.get(key, next(iter(modes))))
            self.selected_key = key
        for key, widget in self.fields.items():
            enabled = generated if key in {"seed", "region", "regions", "importance", "region_sizes", "region_importances"} else play
            if key == "profile":
                widget.configure(state="normal" if play else "disabled")
                continue
            if key == "mode":
                enabled = play and len(modes) > 1
            if key in {"zoom", "paused", "debug", "map_open"} and self.values["headless"].get():
                enabled = False
            state = ("normal" if key in {"seed", "frames", "zoom", "headless", "map_open", "region_sizes", "region_importances"} else "readonly") if enabled else "disabled"
            widget.configure(state=state)
        self.random_button.configure(state="normal" if generated else "disabled")
        self.start_button.configure(state="normal" if target and self.process is None else "disabled")
        if self.process is None:
            self.status.set(target["name"] if target else "개별 테스트를 선택하세요")

    def write(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        if int(self.log.index("end-1c").split(".")[0]) > 1500:
            self.log.delete("1.0", "500.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def start(self):
        selection = self.tree.selection()
        if self.process is not None or not selection or selection[0] not in self.targets:
            return
        target = self.targets[selection[0]]
        try:
            command = build_command(target, {key: variable.get() for key, variable in self.values.items()})
            self.process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding="utf-8", errors="replace",
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.profiled_process = target["kind"] != "test" and self.values["profile"].get()
        except (ValueError, OSError) as error:
            messagebox.showerror("실행할 수 없습니다", str(error), parent=self.root)
            return
        process = self.process
        self.write(f"\n▶ {target['name']}\n")
        self.status.set("실행 중 · " + target["name"])
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")

        def read_output():
            with process.stdout:
                for line in process.stdout:
                    self.output.put(("line", line))
            self.output.put(("exit", process.wait()))

        Thread(target=read_output, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, value = self.output.get_nowait()
                if kind == "line":
                    self.write(value)
                else:
                    self.process = None
                    self.write(f"종료 코드: {value}\n")
                    self.stop_button.configure(state="disabled")
                    self.select()
                    self.status.set("실행 완료" if value == 0 else "실행 종료 · 오류 또는 중단 — 로그 확인")
        except Empty:
            pass
        self.root.after(100, self.poll)

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            if self.profiled_process and sys.platform == "win32":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.process.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
            else:
                self.process.terminate()

    def close(self):
        self.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    Launcher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
