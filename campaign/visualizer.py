"""실제 캠페인 코어를 구동하는 RTS형 군수 관측 GUI."""

import argparse
from collections import Counter
from time import perf_counter
import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path

from .mapview import ACCENT, BACKGROUND, FONT, MUTED, TEXT, StrategicMap
from .persistence import load_campaign, save_campaign
from .playback import Playback
from .resources import TERMINAL_PHASES, available
from .routes import position
from .scenario import DEFAULT_SCENARIO, load_scenario
from .simulation import Simulation
from .viewmodel import world_clock
from .viewer_actions import allowed_commands, build_action


PHASES = {"loading": "집결·적재", "outbound": "운송 중", "unloading": "하역 중",
          "turnaround": "회차 대기", "returning": "귀환 중", "completed": "종료",
          "cancelled": "취소", "stranded": "고립", "destroyed": "격파"}
RESOURCES = {"supplies": "보급품", "ammunition": "탄약", "repair_parts": "수리자재"}
EVENTS = {"reserved": "수송 편성", "departed": "출발", "handed_over": "보급 인계",
          "exported": "외부 인도", "returned": "귀환", "road_discovered": "차단 발견",
          "transport_aborted": "운송 중단", "cancelled": "취소", "shortage": "보급 부족",
          "action": "디버그 개입", "export_demand": "출하 수요 발생"}


class Visualizer(tk.Tk):
    def __init__(self, simulation: Simulation, *, speed=120, paused=False, autostart=True):
        super().__init__()
        self.title("Revenant : L.O.S.T — 군수 전략 관측기")
        self.geometry(f"{min(1440, self.winfo_screenwidth() - 80)}x{min(900, self.winfo_screenheight() - 80)}")
        self.minsize(1080, 720)
        self.configure(background=BACKGROUND)
        self.playback = Playback(simulation, speed=speed)
        self.playback.set_paused(paused)
        self.selection = None
        self._after_id = None
        self._last_frame = perf_counter()
        self._last_panel = 0.0
        self._panel_time = -1
        self._style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<space>", self._space)
        self.bind("<Home>", lambda event: self.map.fit())
        self.bind("<KeyPress-f>", lambda event: self.focus_selection())
        self.after_idle(self._initial_render)
        if autostart:
            self._after_id = self.after(16, self._frame)

    @property
    def state(self):
        return self.playback.simulation.state

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BACKGROUND)
        style.configure("TLabel", background=BACKGROUND, foreground=TEXT, font=(FONT, 9))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", foreground=ACCENT, font=(FONT, 13, "bold"))
        style.configure("Clock.TLabel", font=(FONT, 16, "bold"))
        style.configure("TButton", background="#263b48", foreground=TEXT, padding=(10, 6), font=(FONT, 9))
        style.map("TButton", background=[("active", "#365264")])
        style.configure("Treeview", background="#14232e", fieldbackground="#14232e",
                        foreground=TEXT, rowheight=25, borderwidth=0, font=(FONT, 9))
        style.configure("Treeview.Heading", background="#263b48", foreground=TEXT, font=(FONT, 9))
        style.map("Treeview", background=[("selected", "#345564")])
        style.configure("TNotebook", background=BACKGROUND, borderwidth=0)
        style.configure("TNotebook.Tab", background="#263b48", foreground=TEXT, padding=(10, 5))
        style.map("TNotebook.Tab", background=[("selected", "#345564")])
        style.configure("TCombobox", fieldbackground="#263b48", foreground=TEXT,
                        background="#263b48", arrowcolor=TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", "#263b48")],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", "#263b48")],
                  selectforeground=[("readonly", TEXT)])
        style.configure("TSpinbox", fieldbackground="#263b48", foreground=TEXT,
                        background="#263b48", arrowcolor=TEXT)
        self.option_add("*TCombobox*Listbox.background", "#263b48")
        self.option_add("*TCombobox*Listbox.foreground", TEXT)

    def _build(self):
        header = ttk.Frame(self, padding=(16, 10))
        header.pack(fill="x")
        ttk.Label(header, text="REVENANT / LOGISTICS", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  STRATEGIC OBSERVER", style="Muted.TLabel").pack(side="left")
        self.clock_text = tk.StringVar()
        ttk.Label(header, textvariable=self.clock_text, style="Clock.TLabel").pack(side="right")
        toolbar = ttk.Frame(self, padding=(12, 0, 12, 8))
        toolbar.pack(fill="x")
        self.pause_button = ttk.Button(toolbar, command=self.toggle_pause)
        self.pause_button.pack(side="left", padx=3)
        self.speed_text = tk.StringVar(value=str(int(self.playback.speed)))
        speed = ttk.Combobox(toolbar, textvariable=self.speed_text, values=(1, 30, 120, 600, 3600),
                             state="readonly", width=6)
        speed.pack(side="left", padx=(8, 2))
        speed.bind("<<ComboboxSelected>>", lambda event: self.playback.set_speed(float(self.speed_text.get())))
        ttk.Label(toolbar, text="배속", style="Muted.TLabel").pack(side="left", padx=(0, 10))
        ttk.Button(toolbar, text="1초 진행", command=self.step).pack(side="left", padx=3)
        for label, seconds in (("+1분", 60), ("+1시간", 3600), ("+6시간", 21600)):
            ttk.Button(toolbar, text=label, command=lambda value=seconds: self.playback.request_skip(value)).pack(side="left", padx=3)
        ttk.Button(toolbar, text="스킵 취소", command=self.playback.cancel_skip).pack(side="left", padx=3)
        ttk.Button(toolbar, text="전체 지도 [Home]", command=lambda: self.map.fit()).pack(side="right", padx=3)
        ttk.Button(toolbar, text="불러오기", command=self.load_dialog).pack(side="right", padx=3)
        ttk.Button(toolbar, text="저장", command=self.save_dialog).pack(side="right", padx=3)
        self.statistics = tk.StringVar()
        ttk.Label(self, textvariable=self.statistics, padding=(16, 6), style="Muted.TLabel").pack(fill="x")
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12)
        sidebar = ttk.Frame(body, width=330)
        sidebar.pack(side="right", fill="y", padx=(12, 0))
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(2, weight=1)
        self.map = StrategicMap(body, self.state, on_select=self.select)
        self.map.pack(side="left", fill="both", expand=True)
        notebook = ttk.Notebook(sidebar, height=150)
        notebook.grid(row=0, column=0, sticky="ew")
        self.facilities = self._tree(notebook, "시설", ("병력", "차량"))
        self.operations = self._tree(notebook, "수송대", ("상태", "화물"))
        self.facilities.bind("<<TreeviewSelect>>", lambda event: self._tree_select(self.facilities, "facility"))
        self.operations.bind("<<TreeviewSelect>>", lambda event: self._tree_select(self.operations, "operation"))
        self.facilities.bind("<Double-Button-1>", lambda event: self.focus_selection())
        self.operations.bind("<Double-Button-1>", lambda event: self.focus_selection())
        ttk.Label(sidebar, text="선택 정보", style="Title.TLabel", padding=(0, 8, 0, 5)).grid(row=1, column=0, sticky="w")
        self.details = tk.Text(sidebar, height=6, wrap="word", background="#14232e", foreground=TEXT,
                               relief="flat", padx=12, pady=10, font=(FONT, 9), state="disabled")
        self.details.grid(row=2, column=0, sticky="nsew")
        controls = ttk.Frame(sidebar, padding=(0, 8))
        controls.grid(row=3, column=0, sticky="ew")
        ttk.Label(controls, text="디버그 개입", style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        quantity = ttk.Frame(controls)
        quantity.grid(row=1, column=0, columnspan=2, sticky="ew", pady=5)
        self.resource_text = tk.StringVar(value="보급품")
        resource = ttk.Combobox(quantity, textvariable=self.resource_text,
                                values=tuple(RESOURCES.values()), state="readonly", width=10)
        resource.pack(side="left")
        self.amount_text = tk.StringVar(value="10")
        ttk.Spinbox(quantity, textvariable=self.amount_text, from_=1, to=10000, width=7).pack(side="left", padx=5)
        ttk.Label(quantity, text="개").pack(side="left")
        self.action_buttons = {}
        actions = (("capture", "선택 물자 탈취"), ("destroy_stock", "선택 물자 파괴"),
                   ("toggle_road", "도로 차단 / 개방"), ("damage_facility", "시설 피해 +25%"),
                   ("destroy_vehicle", "차량 1대 격파"), ("damage_unit", "호위 1명 손실"),
                   ("return_operation", "귀환 / 예약 취소"))
        for index, (command, label) in enumerate(actions):
            button = ttk.Button(controls, text=label, command=lambda value=command: self.intervene(value))
            button.grid(row=2 + index // 2, column=index % 2, sticky="ew", padx=2, pady=2)
            self.action_buttons[command] = button
        controls.columnconfigure((0, 1), weight=1)
        ttk.Label(sidebar, text="최근 세계 사건", padding=(0, 5, 0, 4)).grid(row=4, column=0, sticky="w")
        self.events_text = tk.StringVar()
        ttk.Label(sidebar, textvariable=self.events_text, style="Muted.TLabel",
                  justify="left", wraplength=320).grid(row=5, column=0, sticky="ew", pady=(0, 8))
        self.status = tk.StringVar(value="좌클릭 선택 · 더블클릭/F 확대 · 우/중클릭 드래그 · 휠 줌 · WASD 이동 · Home 전체 지도 · Space 재생/정지")
        ttk.Label(self, textvariable=self.status, padding=(14, 8), style="Muted.TLabel").pack(fill="x")

    def _tree(self, notebook, title, columns):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        tree = ttk.Treeview(frame, columns=("value", "extra"), height=7, selectmode="browse")
        tree.heading("#0", text="ID")
        tree.column("#0", width=143, minwidth=100)
        for key, label, width in zip(("value", "extra"), columns, (85, 48)):
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=40, anchor="center")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scrollbar.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        return tree

    def _initial_render(self):
        self.map.focus_region()
        self.map.render(self.playback.markers())
        self.refresh_panel(force=True)

    def toggle_pause(self):
        self.playback.set_paused(not self.playback.paused)
        self.refresh_panel(force=True)

    def _space(self, event):
        if event.widget.winfo_class() in {"Text", "Entry", "TEntry", "TCombobox", "TSpinbox", "TButton"}:
            return None
        self.toggle_pause()
        return "break"

    def step(self):
        self.playback.set_paused(True)
        self.playback.request_skip(1)

    def select(self, selection):
        self.selection = self.map.selection = selection
        self.refresh_panel(force=True)

    def _tree_select(self, tree, kind):
        selection = tree.selection()
        if selection:
            self.select((kind, selection[0]))

    def focus_selection(self):
        if self.selection is None:
            self.map.focus_region()
        else:
            kind, key = self.selection
            if kind == "facility":
                point = self.state.nodes[self.state.facilities[key].node_id].position
            elif kind == "operation":
                point = position(self.state, self.state.operations[key])
            else:
                point = self.state.nodes[self.state.roads[key].start].position
            self.map.focus_world(point.x, point.y)

    def intervene(self, command):
        try:
            resource = next(key for key, label in RESOURCES.items() if label == self.resource_text.get())
            amount = int(self.amount_text.get()) if command in {"capture", "destroy_stock"} else 1
            action = build_action(self.state, self.selection, command, resource=resource, amount=amount)
            self.playback.simulation.apply_action(action)
        except (ValueError, KeyError, StopIteration) as exc:
            self.status.set(f"개입 실패: {exc}")
            return
        self.playback.refresh()
        self.map.dirty = True
        self.map.render(self.playback.markers())
        self.refresh_panel(force=True)
        self.status.set(f"{world_clock(self.state.world_time)}  개입 적용: {action}")

    def save_to(self, path):
        self.playback.set_paused(True)
        save_campaign(self.state, path)
        self.refresh_panel(force=True)
        self.status.set(f"저장 완료: {path}")

    def load_from(self, path):
        state = load_campaign(path)
        self.playback = Playback(Simulation(state), speed=self.playback.speed)
        self.playback.set_paused(True)
        self.map.state = state
        self.selection = self.map.selection = None
        for tree in (self.facilities, self.operations):
            children = tree.get_children()
            if children:
                tree.delete(*children)
        self._panel_time = -1
        self._last_frame = perf_counter()
        self.map.fit()
        self.map.render(self.playback.markers())
        self.refresh_panel(force=True)
        self.status.set(f"불러오기 완료: {path}  /  일시정지 상태")

    def save_dialog(self):
        self.playback.set_paused(True)
        self.refresh_panel(force=True)
        path = filedialog.asksaveasfilename(parent=self, title="캠페인 저장", defaultextension=".json",
                                          filetypes=[("캠페인 저장", "*.json")])
        try:
            if path:
                self.save_to(path)
        except ValueError as exc:
            self.status.set(f"저장 실패: {exc}")
        self._last_frame = perf_counter()

    def load_dialog(self):
        self.playback.set_paused(True)
        self.refresh_panel(force=True)
        path = filedialog.askopenfilename(parent=self, title="캠페인 불러오기",
                                         filetypes=[("캠페인 저장", "*.json")])
        try:
            if path:
                self.load_from(path)
        except ValueError as exc:
            self.status.set(f"불러오기 실패: {exc}")
        self._last_frame = perf_counter()

    def _frame(self):
        started = perf_counter()
        elapsed, self._last_frame = started - self._last_frame, started
        try:
            self.playback.update(elapsed)
        except ValueError as exc:
            self.playback.set_paused(True)
            self.status.set(f"시뮬레이션 중지: {exc}")
        self.map.render(self.playback.markers())
        self.clock_text.set(world_clock(self.state.world_time))
        if started - self._last_panel >= 0.25:
            self.refresh_panel()
            self._last_panel = started
        self._after_id = self.after(max(1, 16 - int((perf_counter() - started) * 1000)), self._frame)

    def refresh_panel(self, *, force=False):
        state = self.state
        self.clock_text.set(world_clock(state.world_time))
        self.pause_button.configure(text="재생 [Space]" if self.playback.paused else "일시정지 [Space]")
        if not force and self._panel_time == state.world_time:
            return
        self._panel_time = state.world_time
        commands = allowed_commands(state, self.selection)
        for command, button in self.action_buttons.items():
            button.configure(state="normal" if command in commands else "disabled")
        active = {op.id: op for op in state.operations.values() if op.phase not in TERMINAL_PHASES}
        vehicle_states = Counter(vehicle.status for vehicle in state.vehicles.values())
        mode = "유한" if state.source.mode == "finite" else "무한"
        pending = (f"  |  스킵 목표 {world_clock(self.playback.skip_target)}"
                   if self.playback.skip_target is not None else "")
        self.statistics.set(f"외부 지원 {mode}  |  진행 작전 {len(active)}  |  가용 차량 {vehicle_states['available']}  |  "
                            f"외부 인도 {sum(state.demand.delivered.values())}  |  보급품 소비 {state.ledger.consumed['supplies']}{pending}")
        for key in self.operations.get_children():
            if key not in active:
                self.operations.delete(key)
        for operation in active.values():
            reservation = state.reservations.get(operation.id)
            cargo = reservation.cargo if reservation else operation.cargo
            values = (PHASES[operation.phase], sum(cargo.values()))
            if self.operations.exists(operation.id):
                self.operations.item(operation.id, values=values)
            else:
                self.operations.insert("", "end", iid=operation.id, text=operation.id, values=values)
        for facility in state.facilities.values():
            soldiers = sum(unit.soldiers for unit in state.units.values() if unit.node_id == facility.node_id)
            vehicles = sum(vehicle.node_id == facility.node_id and vehicle.status != "destroyed"
                           for vehicle in state.vehicles.values())
            if self.facilities.exists(facility.id):
                self.facilities.item(facility.id, values=(soldiers, vehicles))
            else:
                self.facilities.insert("", "end", iid=facility.id, text=facility.id, values=(soldiers, vehicles))
        lines = self._selection_lines()
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", "\n".join(lines))
        self.details.configure(state="disabled")
        events = [event for event in state.history[-100:] if event["kind"] in EVENTS][-5:]
        self.events_text.set("\n".join(f"{world_clock(event['time']).split()[-1]}  {EVENTS[event['kind']]} "
                                     f"{event.get('operation_id', event.get('facility_id', event.get('road_id', '')))}"
                                     for event in events) or "예정된 세계 사건을 기다리는 중")

    def _selection_lines(self):
        state = self.state
        if self.selection is None:
            return ["시설·수송대·도로를 선택하세요.", "", "실제 캠페인 상태를 표시합니다.",
                    "WH: 창고 / HQ: 본부 / C: 통신", "수송대 화살표: 실제 이동 방향", "", "외부 인도 실적", *self._stock_lines(state.demand.delivered),
                    "", "미충족 출하 수요", *self._stock_lines(state.demand.outstanding)]
        kind, key = self.selection
        if kind == "facility":
            facility = state.facilities[key]
            lines = [key, f"지역 {facility.region_id.upper()} / 소유 {state.regions[facility.region_id].owner}",
                     f"시설 피해 {facility.damage}%", "", "실제 재고 / 예약 / 목표"]
            for name, label in RESOURCES.items():
                reserved = facility.inventory[name] - available(state, key, name)
                lines.append(f"{label}: {facility.inventory[name]} / {reserved} / {facility.target_stock[name]}")
            return lines + ["", "시설 기능", ", ".join(facility.capabilities)]
        if kind == "road":
            road = state.roads[key]
            return [key, f"{road.start} → {road.end}", f"도로 길이 {road.length_m:g} m",
                    f"현재 상태: {'차단' if road.blocked else '통행 가능'}", "",
                    "수송대는 현장에서 차단을 확인한 뒤", "우회·대기·귀환합니다."]
        operation = state.operations[key]
        reservation = state.reservations.get(key)
        cargo = reservation.cargo if reservation else operation.cargo
        provisions = reservation.provisions if reservation else operation.provisions
        return [key, f"{'외부 출하' if operation.kind == 'export' else '보급'} / {PHASES[operation.phase]}",
                operation.reason, f"{operation.origin_id} → {operation.destination_id}",
                f"대기 사유: {operation.waiting_reason or '없음'}", "",
                "예약 화물" if reservation else "실제 화물", *self._stock_lines(cargo),
                "", "운행·호위 유지 물자", *self._stock_lines(provisions), "",
                "차량: " + ", ".join(operation.vehicle_ids), "호위: " + ", ".join(operation.unit_ids)]

    @staticmethod
    def _stock_lines(stock):
        return [f"{label}: {stock[name]}" for name, label in RESOURCES.items()]

    def close(self):
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        self.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="RTS형 캠페인 시뮬레이션 비주얼라이저")
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--support", choices=("finite", "infinite"))
    parser.add_argument("--seed", type=int)
    parser.add_argument("--load", type=Path, dest="load_path")
    parser.add_argument("--speed", type=int, choices=(1, 30, 120, 600, 3600), default=120)
    parser.add_argument("--paused", action="store_true")
    args = parser.parse_args(argv)
    if args.load_path and any(value is not None for value in (args.scenario, args.support, args.seed)):
        parser.error("--load는 --scenario·--support·--seed와 함께 사용할 수 없음")
    try:
        state = (load_campaign(args.load_path) if args.load_path else load_scenario(
            args.scenario or DEFAULT_SCENARIO, support=args.support or "finite",
            seed=args.seed if args.seed is not None else 42))
        simulation = Simulation(state)
        if not args.load_path:
            simulation.advance_to(state.world_time)
        app = Visualizer(simulation, speed=args.speed, paused=args.paused)
        app.mainloop()
        return 0
    except (ValueError, KeyError, TypeError, OSError, tk.TclError) as exc:
        parser.exit(2, f"비주얼라이저 실행 실패: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
