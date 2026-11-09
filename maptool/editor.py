"""Small, task-focused GUI for authoring Revenant: L.O.S.T modules.

This is intentionally not a general-purpose art editor. Tiled remains the
source for visual tile layers, while this editor adds semantic wall edges,
doors, and AI objects. Wall painting does not block a whole floor cell.
"""

from __future__ import annotations

import json
from pathlib import Path
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any
import xml.etree.ElementTree as ET

from PIL import Image, ImageTk

from .assets import ASSET_REQUIREMENTS, build_asset_atlas, inspect_source_assets
from .compiler import compile_module, create_template_map, write_ai_package, write_runtime


TILE_SIZE = 32
PIXELS_PER_METER = 64
TILE_METERS = TILE_SIZE / PIXELS_PER_METER
WALL_THICKNESS_M = 0.375
DEFAULT_WIDTH = 32
DEFAULT_HEIGHT = 24
OBJECT_KINDS = (
    "enemy_spawn",
    "patrol_node",
    "guard_point",
    "cover_point",
    "connector",
    "door",
    "window",
    "stairs",
    "decoration",
)


def _tiled_properties(properties: Any) -> dict[str, Any]:
    if isinstance(properties, dict):
        return dict(properties)
    result: dict[str, Any] = {}
    for item in properties or []:
        if isinstance(item, dict) and "name" in item:
            result[item["name"]] = item.get("value")
    return result


def _property_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


def _property_list(properties: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for name, value in properties.items():
        if isinstance(value, list):
            value = json.dumps(value, ensure_ascii=False)
        result.append({"name": name, "type": _property_type(value), "value": value})
    return result


class MapEditor(tk.Tk):
    def __init__(self, map_path: Path | None = None) -> None:
        super().__init__()
        self.title("Revenant: L.O.S.T — Module Editor")
        self.geometry("1280x800")
        self.minsize(960, 600)

        self.map_path: Path | None = None
        self.tileset_path: Path | None = None
        self.tileset_image: Image.Image | None = None
        self.tiles: list[Image.Image] = []
        self.tile_metadata: dict[int, dict[str, Any]] = {}
        self.entity_assets: dict[str, dict[str, Any]] = {}
        self.entity_images: dict[str, Image.Image] = {}
        self.preview_items: list[int] = []
        self.preview_cell: tuple[int, int] | None = None
        self.palette_gids: list[int] = []
        self.tile_cache: dict[tuple[int, int], ImageTk.PhotoImage] = {}
        self.palette_images: list[ImageTk.PhotoImage] = []
        self.canvas_images: list[ImageTk.PhotoImage] = []
        self.view_tile_px = 32
        self.width = DEFAULT_WIDTH
        self.height = DEFAULT_HEIGHT
        self.ground: list[int] = []
        self.architecture: list[int] = []
        self.decoration: list[int] = []
        self.wall_edges: list[dict[str, Any]] = []
        self.collision: list[bool] = []
        self.occluder: list[bool] = []
        self.objects: list[dict[str, Any]] = []
        self.nav_nodes: list[dict[str, Any]] = []
        self.nav_edges: list[dict[str, Any]] = []
        self.selected_nav_node: int | None = None
        self.next_object_id = 1
        self.map_props: dict[str, Any] = {
            "module_id": "new_module",
            "module_type": "module",
            "variant_id": "new_module",
            "wall_thickness_m": WALL_THICKNESS_M,
        }

        self.tool = tk.StringVar(value="ground")
        self.object_kind = tk.StringVar(value="enemy_spawn")
        self.door_orientation = tk.StringVar(value="horizontal")
        self.door_hinge = tk.StringVar(value="left")
        self.nav_node_kind = tk.StringVar(value="corridor")
        self.nav_edge_mode = tk.StringVar(value="normal")
        self.palette_filter = tk.StringVar(value="all")
        self.selected_tile = 0
        self.status = tk.StringVar(value="새 모듈입니다. 타일셋을 불러오세요.")

        self._build_ui()
        self._new_map(self.width, self.height)
        if map_path:
            self.load_map(map_path)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=4)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="새 모듈", command=self.new_map_dialog).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="열기", command=self.open_map_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="저장", command=self.save_map_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="타일셋 불러오기", command=self.load_tileset_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="원본 자산 패킹", command=self.open_asset_builder).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="설정", command=self.settings_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="컴파일", command=self.compile_current).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(toolbar, text="확대", command=lambda: self.set_zoom(self.view_tile_px + 8)).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="축소", command=lambda: self.set_zoom(max(16, self.view_tile_px - 8))).pack(side=tk.LEFT, padx=2)
        ttk.Label(toolbar, textvariable=self.status).pack(side=tk.LEFT, padx=16)

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(body, padding=4, width=340)
        body.add(left, weight=0)
        right = ttk.Frame(body, padding=4)
        body.add(right, weight=1)

        ttk.Label(left, text="제작 모드").pack(anchor=tk.W)
        modes = ttk.Frame(left)
        modes.pack(fill=tk.X, pady=(2, 8))
        for label, value in (("지면", "ground"), ("벽", "wall"), ("지우개", "erase"), ("오브젝트", "object"), ("이동 그래프", "navigation")):
            ttk.Radiobutton(modes, text=label, value=value, variable=self.tool, command=self._mode_changed).pack(side=tk.LEFT)

        object_frame = ttk.Frame(left)
        object_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(object_frame, text="오브젝트:").pack(side=tk.LEFT)
        self.object_combo = ttk.Combobox(object_frame, textvariable=self.object_kind, values=OBJECT_KINDS, state="readonly", width=18)
        self.object_combo.pack(side=tk.LEFT, padx=4)
        self.object_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_entity_assets())

        asset_frame = ttk.Frame(left)
        asset_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(asset_frame, text="자산:").pack(side=tk.LEFT)
        self.entity_asset = tk.StringVar()
        self.entity_asset_combo = ttk.Combobox(asset_frame, textvariable=self.entity_asset, state="readonly", width=22)
        self.entity_asset_combo.pack(side=tk.LEFT, padx=4)

        door_frame = ttk.Frame(left)
        door_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(door_frame, text="문 방향:").pack(side=tk.LEFT)
        ttk.Combobox(
            door_frame,
            textvariable=self.door_orientation,
            values=("horizontal", "vertical"),
            state="readonly",
            width=12,
        ).pack(side=tk.LEFT, padx=4)

        hinge_frame = ttk.Frame(left)
        hinge_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(hinge_frame, text="문 힌지:").pack(side=tk.LEFT)
        ttk.Combobox(
            hinge_frame,
            textvariable=self.door_hinge,
            values=("left", "right", "top", "bottom"),
            state="readonly",
            width=10,
        ).pack(side=tk.LEFT, padx=4)

        navigation_frame = ttk.Frame(left)
        navigation_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(navigation_frame, text="노드:").pack(side=tk.LEFT)
        ttk.Combobox(
            navigation_frame,
            textvariable=self.nav_node_kind,
            values=("room", "corridor", "door", "stairs", "connector"),
            state="readonly",
            width=10,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Label(navigation_frame, text="연결:").pack(side=tk.LEFT)
        ttk.Combobox(
            navigation_frame,
            textvariable=self.nav_edge_mode,
            values=("preferred", "normal", "avoid"),
            state="readonly",
            width=10,
        ).pack(side=tk.LEFT, padx=2)

        ttk.Label(left, text="타일셋 팔레트").pack(anchor=tk.W)
        filter_frame = ttk.Frame(left)
        filter_frame.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(filter_frame, text="분류:").pack(side=tk.LEFT)
        self.palette_filter_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.palette_filter,
            values=("all", "ground", "road", "wall", "half_wall"),
            state="readonly",
            width=14,
        )
        self.palette_filter_combo.pack(side=tk.LEFT, padx=4)
        self.palette_filter_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_palette())
        self.palette = tk.Canvas(left, background="#303030", highlightthickness=0, height=560)
        palette_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.palette.yview)
        self.palette.configure(yscrollcommand=palette_scroll.set)
        self.palette.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        palette_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.palette.bind("<Button-1>", self._palette_click)

        canvas_frame = ttk.Frame(right)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        self.map_canvas = tk.Canvas(canvas_frame, background="#777777", highlightthickness=0)
        x_scroll = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=self.map_canvas.xview)
        y_scroll = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.map_canvas.yview)
        self.map_canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self.map_canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        self.map_canvas.bind("<Button-1>", self._map_click)
        self.map_canvas.bind("<B1-Motion>", self._map_drag)
        self.map_canvas.bind("<Button-3>", self._delete_object_at)
        self.map_canvas.bind("<Motion>", self._map_motion)

    def _new_map(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        size = width * height
        self.ground = [0] * size
        self.architecture = [0] * size
        self.decoration = [0] * size
        self.wall_edges = []
        self.collision = [False] * size
        self.occluder = [False] * size
        self.objects = []
        self.nav_nodes = []
        self.nav_edges = []
        self.selected_nav_node = None
        self.next_object_id = 1
        self.refresh_map()

    def new_map_dialog(self) -> None:
        width = simpledialog.askinteger("새 모듈", "가로 타일 수", initialvalue=self.width, minvalue=1, parent=self)
        if width is None:
            return
        height = simpledialog.askinteger("새 모듈", "세로 타일 수", initialvalue=self.height, minvalue=1, parent=self)
        if height is None:
            return
        self.map_path = None
        self.map_props = {"module_id": "new_module", "module_type": "module", "variant_id": "new_module", "wall_thickness_m": WALL_THICKNESS_M}
        self._new_map(width, height)
        self.status.set(f"새 모듈 {width}×{height}")

    def open_map_dialog(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Tiled map", "*.tmj *.json"), ("All files", "*.*")])
        if path:
            self.load_map(Path(path))

    def load_tileset_dialog(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg"), ("All files", "*.*")])
        if path:
            self.load_tileset(Path(path))

    def load_tileset(self, path: Path) -> None:
        image = Image.open(path).convert("RGBA")
        columns = image.width // TILE_SIZE
        rows = image.height // TILE_SIZE
        if columns < 1 or rows < 1:
            messagebox.showerror("타일셋 오류", "이미지가 64×64 타일 하나보다 작습니다.", parent=self)
            return
        self.tileset_path = path.resolve()
        self.tileset_image = image
        self.tile_metadata = {}
        self.entity_assets = {}
        self.entity_images = {}
        manifest_candidates = [
            path.with_name(f"{path.stem}.manifest.json"),
            path.with_suffix(".manifest.json"),
        ]
        for manifest_path in manifest_candidates:
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.tile_metadata = {
                    int(item["gid"]): item
                    for item in manifest.get("tiles", [])
                    if "gid" in item
                }
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                self.tile_metadata = {}
            break
        entity_manifest = path.parent / "entities.manifest.json"
        if entity_manifest.is_file():
            try:
                entity_data = json.loads(entity_manifest.read_text(encoding="utf-8"))
                self.entity_assets = {
                    str(item["asset_id"]): item
                    for item in entity_data.get("assets", [])
                    if "asset_id" in item
                }
                for asset_id, item in self.entity_assets.items():
                    asset_path = (entity_manifest.parent / item.get("file", "")).resolve()
                    if asset_path.is_file():
                        try:
                            self.entity_images[asset_id] = Image.open(asset_path).convert("RGBA")
                        except OSError:
                            pass
            except (OSError, json.JSONDecodeError, TypeError):
                self.entity_assets = {}
        self.tiles = [
            image.crop((x * TILE_SIZE, y * TILE_SIZE, (x + 1) * TILE_SIZE, (y + 1) * TILE_SIZE))
            for y in range(rows)
            for x in range(columns)
        ]
        self.tile_cache.clear()
        self.refresh_palette()
        self._refresh_entity_assets()
        self.status.set(f"타일셋: {path.name} ({columns}×{rows})")

    def _refresh_entity_assets(self) -> None:
        if not hasattr(self, "entity_asset_combo"):
            return
        kind = self.object_kind.get()
        values = [
            asset_id for asset_id, asset in self.entity_assets.items()
            if asset.get("category") == kind
        ]
        self.entity_asset_combo["values"] = values
        if self.entity_asset.get() not in values:
            self.entity_asset.set(values[0] if values else "")

    def load_tileset_reference(self, map_path: Path, tileset: dict[str, Any]) -> None:
        image_path = tileset.get("image")
        if image_path:
            candidate = (map_path.parent / image_path).resolve()
            if candidate.is_file():
                self.load_tileset(candidate)
            return
        source = tileset.get("source")
        if not source:
            return
        tsx_path = (map_path.parent / source).resolve()
        if not tsx_path.is_file():
            return
        try:
            root = ET.parse(tsx_path).getroot()
            image = root.find("image")
            image_source = image.get("source") if image is not None else None
        except (OSError, ET.ParseError):
            return
        if image_source:
            candidate = (tsx_path.parent / image_source).resolve()
            if candidate.is_file():
                self.load_tileset(candidate)

    def refresh_palette(self) -> None:
        self.palette.delete("all")
        self.palette_images = []
        category = self.palette_filter.get()
        if category == "all":
            self.palette_gids = list(range(1, len(self.tiles) + 1))
        else:
            self.palette_gids = [
                gid for gid in sorted(self.tile_metadata)
                if self.tile_metadata[gid].get("category") == category
            ]
        cell = 48
        columns = 6
        for index, gid in enumerate(self.palette_gids):
            tile = self.tiles[gid - 1]
            image = ImageTk.PhotoImage(tile.resize((cell, cell), Image.Resampling.NEAREST))
            self.palette_images.append(image)
            x = (index % columns) * cell + cell // 2
            y = (index // columns) * cell + cell // 2
            self.palette.create_image(x, y, image=image, tags=(f"tile:{index}",))
            self.palette.create_rectangle(x - cell // 2, y - cell // 2, x + cell // 2, y + cell // 2, outline="#888888")
        self.palette.configure(scrollregion=(0, 0, columns * cell, ((len(self.palette_gids) + columns - 1) // columns) * cell))

    def _palette_click(self, event: tk.Event) -> None:
        cell = 48
        index = int(self.palette.canvasx(event.x) // cell) + int(self.palette.canvasy(event.y) // cell) * 6
        if 0 <= index < len(self.palette_gids):
            self.selected_tile = self.palette_gids[index]
            self.tool.set("wall" if self.tool.get() == "wall" else "ground")
            self.status.set(f"타일 선택: {index + 1}")
            self.refresh_palette_selection()

    def refresh_palette_selection(self) -> None:
        self.palette.delete("selection")
        if self.selected_tile <= 0 or self.selected_tile not in self.palette_gids:
            return
        index = self.palette_gids.index(self.selected_tile)
        cell = 48
        x = (index % 6) * cell
        y = (index // 6) * cell
        self.palette.create_rectangle(x + 1, y + 1, x + cell - 1, y + cell - 1, outline="#ffff00", width=3, tags="selection")

    def open_asset_builder(self) -> None:
        window = tk.Toplevel(self)
        window.title("원본 자산 → 타일셋 생성")
        window.geometry("760x520")
        window.transient(self)

        source_var = tk.StringVar()
        output_var = tk.StringVar(value=str(Path("assets") / "generated"))
        name_var = tk.StringVar(value="revenant_tileset")
        status_var = tk.StringVar(value="원본 폴더를 선택하세요.")

        form = ttk.Frame(window, padding=8)
        form.pack(fill=tk.X)
        ttk.Label(form, text="원본 폴더").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=source_var, width=62).grid(row=0, column=1, padx=4, pady=3)
        ttk.Button(form, text="찾기", command=lambda: self._choose_asset_source(source_var, refresh)).grid(row=0, column=2, pady=3)
        ttk.Label(form, text="출력 폴더").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=output_var, width=62).grid(row=1, column=1, padx=4, pady=3)
        ttk.Button(form, text="찾기", command=lambda: self._choose_directory(output_var)).grid(row=1, column=2, pady=3)
        ttk.Label(form, text="타일셋 이름").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=name_var, width=30).grid(row=2, column=1, sticky="w", padx=4, pady=3)
        ttk.Button(form, text="필요 폴더 만들기", command=lambda: self._create_asset_folders(source_var, refresh)).grid(row=2, column=2, pady=3)

        ttk.Label(window, text="원본 이미지 규격").pack(anchor="w", padx=8)
        columns = ("category", "size", "count", "status")
        table = ttk.Treeview(window, columns=columns, show="headings", height=12)
        table.heading("category", text="종류")
        table.heading("size", text="필요 규격")
        table.heading("count", text="파일 수")
        table.heading("status", text="상태")
        table.column("category", width=140)
        table.column("size", width=250)
        table.column("count", width=70, anchor="center")
        table.column("status", width=220)
        table.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        def refresh() -> None:
            table.delete(*table.get_children())
            if not source_var.get():
                status_var.set("원본 폴더를 선택하세요.")
                return
            rows = inspect_source_assets(Path(source_var.get()))
            for row in rows:
                status = "정상" if row["ok"] else "; ".join(row["errors"])
                table.insert("", tk.END, values=(row["label"], row["required_size"], row["count"], status))
            status_var.set("규격 오류가 있으면 해당 파일을 수정하세요.")

        buttons = ttk.Frame(window, padding=8)
        buttons.pack(fill=tk.X)
        ttk.Label(buttons, textvariable=status_var).pack(side=tk.LEFT)
        ttk.Button(buttons, text="검사", command=refresh).pack(side=tk.RIGHT, padx=3)

        def build() -> None:
            if not source_var.get() or not output_var.get():
                messagebox.showwarning("입력 필요", "원본 폴더와 출력 폴더를 선택하세요.", parent=window)
                return
            try:
                outputs = build_asset_atlas(Path(source_var.get()), Path(output_var.get()), name_var.get().strip() or "revenant_tileset")
            except (OSError, ValueError) as exc:
                messagebox.showerror("타일셋 생성 실패", str(exc), parent=window)
                refresh()
                return
            self.load_tileset(outputs["image"])
            self.status.set(f"타일셋 생성됨: {outputs['image'].name}")
            window.destroy()

        ttk.Button(buttons, text="타일셋 생성", command=build).pack(side=tk.RIGHT)

    def _choose_directory(self, variable: tk.StringVar) -> None:
        selected = filedialog.askdirectory(parent=self)
        if selected:
            variable.set(selected)

    def _choose_asset_source(self, variable: tk.StringVar, refresh: Any) -> None:
        selected = filedialog.askdirectory(parent=self)
        if selected:
            variable.set(selected)
            refresh()

    def _create_asset_folders(self, variable: tk.StringVar, refresh: Any) -> None:
        if not variable.get():
            selected = filedialog.askdirectory(parent=self)
            if not selected:
                return
            variable.set(selected)
        root = Path(variable.get())
        root.mkdir(parents=True, exist_ok=True)
        for category in ASSET_REQUIREMENTS:
            (root / category).mkdir(exist_ok=True)
        refresh()

    def _mode_changed(self) -> None:
        self.status.set(f"모드: {self.tool.get()}")

    def set_zoom(self, value: int) -> None:
        self.view_tile_px = max(16, min(96, value))
        self.tile_cache.clear()
        self.refresh_map()

    def _cell_from_event(self, event: tk.Event) -> tuple[int, int] | None:
        x = int(self.map_canvas.canvasx(event.x) // self.view_tile_px)
        y = int(self.map_canvas.canvasy(event.y) // self.view_tile_px)
        if 0 <= x < self.width and 0 <= y < self.height:
            return x, y
        return None

    def _map_click(self, event: tk.Event) -> None:
        self._edit_cell(self._cell_from_event(event))

    def _map_motion(self, event: tk.Event) -> None:
        self.preview_cell = self._cell_from_event(event)
        self._draw_preview()

    def _map_drag(self, event: tk.Event) -> None:
        if self.tool.get() not in {"object", "navigation"}:
            self._edit_cell(self._cell_from_event(event))

    def _delete_object_at(self, event: tk.Event) -> None:
        cell = self._cell_from_event(event)
        if cell is None:
            return
        x, y = cell
        for index in range(len(self.nav_nodes) - 1, -1, -1):
            node = self.nav_nodes[index]
            if node["x"] == x and node["y"] == y:
                node_id = node["node_id"]
                self.nav_nodes.pop(index)
                self.nav_edges = [edge for edge in self.nav_edges if edge["from_id"] != node_id and edge["to_id"] != node_id]
                if self.selected_nav_node == node_id:
                    self.selected_nav_node = None
                self.status.set(f"이동 노드 삭제: {node_id}")
                self.refresh_map()
                return
        for index in range(len(self.objects) - 1, -1, -1):
            obj = self.objects[index]
            if obj["x"] == x and obj["y"] == y:
                removed = self.objects.pop(index)
                self.status.set(f"오브젝트 삭제: {removed['kind']}")
                self.refresh_map()
                return

    def _edit_cell(self, cell: tuple[int, int] | None) -> None:
        if cell is None:
            return
        x, y = cell
        index = y * self.width + x
        mode = self.tool.get()
        if mode == "navigation":
            self._edit_navigation(x, y)
        elif mode == "ground" and self.selected_tile:
            self.ground[index] = self.selected_tile
        elif mode == "wall" and self.selected_tile:
            self.architecture[index] = self.selected_tile
        elif mode == "decoration" and self.selected_tile:
            self.decoration[index] = self.selected_tile
        elif mode == "erase":
            if self.decoration[index]:
                self.decoration[index] = 0
            elif self.architecture[index]:
                self.architecture[index] = 0
                self.collision[index] = False
                self.occluder[index] = False
                self.wall_edges = [item for item in self.wall_edges if not (item["x"] == x and item["y"] == y)]
            else:
                self.ground[index] = 0
        elif mode == "object":
            self.place_object(x, y)
        self.refresh_map()

    def _wall_cell(self, x: int, y: int) -> bool:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False
        index = y * self.width + x
        return bool(self.architecture[index] or self.collision[index] or self.occluder[index])

    def _can_place_window(self, x: int, y: int) -> bool:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False
        index = y * self.width + x
        tile_info = self.tile_metadata.get(self.architecture[index], {})
        return tile_info.get("category") == "half_wall"

    def _edit_navigation(self, x: int, y: int) -> None:
        existing = next((node for node in self.nav_nodes if node["x"] == x and node["y"] == y), None)
        if existing is None:
            node_id = f"nav_{len(self.nav_nodes) + 1}"
            while any(node["node_id"] == node_id for node in self.nav_nodes):
                node_id = f"nav_{len(self.nav_nodes) + 2}"
            existing = {
                "node_id": node_id,
                "x": x,
                "y": y,
                "kind": self.nav_node_kind.get(),
            }
            self.nav_nodes.append(existing)
            if self.selected_nav_node is not None and self.selected_nav_node != node_id:
                self._connect_nav_nodes(self.selected_nav_node, node_id)
            self.selected_nav_node = node_id
            self.status.set(f"이동 노드 생성: {node_id}")
            return
        if self.selected_nav_node is None:
            self.selected_nav_node = existing["node_id"]
            self.status.set(f"이동 노드 선택: {self.selected_nav_node}")
            return
        if self.selected_nav_node != existing["node_id"]:
            self._connect_nav_nodes(self.selected_nav_node, existing["node_id"])
            self.selected_nav_node = existing["node_id"]

    def _connect_nav_nodes(self, from_id: str, to_id: str) -> None:
        if any(
            edge["from_id"] == from_id and edge["to_id"] == to_id
            for edge in self.nav_edges
        ):
            return
        self.nav_edges.append({
            "from_id": from_id,
            "to_id": to_id,
            "mode": self.nav_edge_mode.get(),
            "cost": 1.0 if self.nav_edge_mode.get() == "preferred" else 3.0 if self.nav_edge_mode.get() == "avoid" else 1.0,
            "width_m": 1.0,
            "one_way": False,
        })

    def place_object(self, x: int, y: int) -> None:
        kind = self.object_kind.get()
        properties: dict[str, Any] = {}
        object_x = x
        object_y = y
        object_width = 1 if kind in {"door", "window"} else 0
        object_height = 1 if kind in {"door", "window"} else 0
        if kind == "enemy_spawn":
            properties["archetype"] = simpledialog.askstring("적 스폰", "archetype", initialvalue="rifleman", parent=self)
            if properties["archetype"] is None:
                return
            properties["group_id"] = simpledialog.askstring("적 스폰", "group_id", initialvalue="default", parent=self) or "default"
            properties["count"] = simpledialog.askinteger("적 스폰", "count", initialvalue=1, minvalue=1, parent=self) or 1
        elif kind == "patrol_node":
            properties["route_id"] = simpledialog.askstring("순찰 노드", "route_id", initialvalue="patrol_01", parent=self)
            if properties["route_id"] is None:
                return
            properties["order"] = simpledialog.askinteger("순찰 노드", "order", initialvalue=0, minvalue=0, parent=self) or 0
            properties["wait_seconds"] = 0
        elif kind == "connector":
            properties["connector_type"] = simpledialog.askstring("연결점", "connector_type", initialvalue="road", parent=self) or "road"
            properties["direction"] = simpledialog.askstring("연결점", "direction", initialvalue="north", parent=self) or "north"
            properties["connector_width"] = 1
        elif kind == "cover_point":
            properties["quality"] = simpledialog.askinteger("엄폐 지점", "quality", initialvalue=1, minvalue=1, maxvalue=3, parent=self) or 1
            properties["facing_degrees"] = 0
        elif kind == "door":
            gap = self._find_door_gap(x, y, self.door_orientation.get())
            if gap is None:
                self.status.set("문은 양쪽 벽 사이의 2타일 빈 공간에만 배치할 수 있습니다.")
                return
            object_x, object_y, object_width, object_height = gap
            asset_id = self.entity_asset.get() or next((key for key, item in self.entity_assets.items() if item.get("category") == "door"), "door_default")
            access = simpledialog.askstring("문 접근 방식", "free 또는 locked", initialvalue="free", parent=self)
            if access is None:
                return
            access = access.strip().lower()
            if access not in {"free", "locked"}:
                self.status.set("문 접근 방식은 free 또는 locked만 사용할 수 있습니다.")
                return
            properties.update({
                "asset_id": asset_id,
                "orientation": self.door_orientation.get(),
                "hinge": self.door_hinge.get(),
                "span_tiles": 2,
                "state": "closed",
                "access": access,
                "locked": access == "locked",
                "closed_angle": 0,
                "open_angle": 90,
                "blocks_movement": True,
                "blocks_vision": True,
                "blocks_sound": True,
                "sound_transmission_closed": 0.0,
                "sound_transmission_open": 1.0,
            })
        elif kind == "window":
            index = y * self.width + x
            if not self._can_place_window(x, y):
                self.status.set("창문은 half_wall 타일 위에만 배치할 수 있습니다.")
                return
            asset_id = self.entity_asset.get() or next((key for key, item in self.entity_assets.items() if item.get("category") == "window"), "window_default")
            properties.update({
                "asset_id": asset_id,
                "state": "intact",
                "blocks_movement": True,
                "blocks_vision": False,
                "blocks_sound": False,
                "sound_transmission_closed": 0.35,
                "sound_transmission_open": 1.0,
            })
        elif kind == "decoration":
            asset_id = self.entity_asset.get()
            asset = self.entity_assets.get(asset_id)
            if asset is None:
                self.status.set("장식 자산을 먼저 선택하세요.")
                return
            object_width = int(asset.get("width_tiles", 1))
            object_height = int(asset.get("height_tiles", 1))
            if x + object_width > self.width or y + object_height > self.height:
                self.status.set("장식물이 맵 범위를 벗어납니다.")
                return
            properties["asset_id"] = asset_id
        self.objects.append({
            "id": self.next_object_id,
            "kind": kind,
            "x": object_x,
            "y": object_y,
            "width": object_width,
            "height": object_height,
            "properties": properties,
        })
        self.next_object_id += 1

    def _find_door_gap(self, x: int, y: int, orientation: str) -> tuple[int, int, int, int] | None:
        candidates = [(x, y)]
        if orientation == "horizontal":
            candidates.append((x - 1, y))
            for start_x, start_y in candidates:
                if start_x < 1 or start_x + 2 >= self.width or not (0 <= start_y < self.height):
                    continue
                gap = [start_y * self.width + start_x, start_y * self.width + start_x + 1]
                if all(not (self.architecture[index] or self.collision[index] or self.occluder[index]) for index in gap):
                    if self._wall_cell(start_x - 1, start_y) and self._wall_cell(start_x + 2, start_y):
                        return start_x, start_y, 2, 1
        else:
            candidates.append((x, y - 1))
            for start_x, start_y in candidates:
                if start_y < 1 or start_y + 2 >= self.height or not (0 <= start_x < self.width):
                    continue
                gap = [start_y * self.width + start_x, (start_y + 1) * self.width + start_x]
                if all(not (self.architecture[index] or self.collision[index] or self.occluder[index]) for index in gap):
                    if self._wall_cell(start_x, start_y - 1) and self._wall_cell(start_x, start_y + 2):
                        return start_x, start_y, 1, 2
        return None

    def refresh_map(self) -> None:
        self.map_canvas.delete("all")
        self.canvas_images = []
        size = self.view_tile_px
        self.map_canvas.configure(scrollregion=(0, 0, self.width * size, self.height * size))
        for y in range(self.height):
            for x in range(self.width):
                index = y * self.width + x
                px = x * size
                py = y * size
                self.map_canvas.create_rectangle(px, py, px + size, py + size, fill="#777777", outline="#999999")
                for gid in (self.ground[index], self.architecture[index], self.decoration[index]):
                    if gid and 0 < gid <= len(self.tiles):
                        cache_key = (gid, size)
                        image = self.tile_cache.get(cache_key)
                        if image is None:
                            image = ImageTk.PhotoImage(self.tiles[gid - 1].resize((size, size), Image.Resampling.NEAREST))
                            self.tile_cache[cache_key] = image
                        self.canvas_images.append(image)
                        self.map_canvas.create_image(px, py, image=image, anchor=tk.NW)
        for obj in self.objects:
            px = (obj["x"] + 0.5) * size
            py = (obj["y"] + 0.5) * size
            color = {
                "enemy_spawn": "#e22",
                "patrol_node": "#2a4",
                "connector": "#26c",
                "cover_point": "#da2",
                "guard_point": "#f80",
                "door": "#c80",
                "window": "#28c",
                "stairs": "#a4c",
            }.get(obj["kind"], "#fff")
            asset_id = obj["properties"].get("asset_id")
            entity_image = self.entity_images.get(asset_id)
            if entity_image is not None and obj["kind"] in {"door", "window", "decoration"}:
                image = entity_image
                if obj["properties"].get("orientation") == "vertical":
                    image = image.transpose(Image.Transpose.ROTATE_90)
                image = ImageTk.PhotoImage(
                    image.resize(
                        (max(1, obj["width"] * size), max(1, obj["height"] * size)),
                        Image.Resampling.NEAREST,
                    )
                )
                self.canvas_images.append(image)
                self.map_canvas.create_image(obj["x"] * size, obj["y"] * size, image=image, anchor=tk.NW)
            elif obj["kind"] in {"door", "window", "decoration"}:
                self.map_canvas.create_rectangle(
                    obj["x"] * size,
                    obj["y"] * size,
                    (obj["x"] + max(1, obj["width"])) * size,
                    (obj["y"] + max(1, obj["height"])) * size,
                    outline=color,
                    width=3,
                )
            else:
                self.map_canvas.create_oval(px - 6, py - 6, px + 6, py + 6, fill=color, outline="#111")
        self._draw_navigation_graph(size)
        self._draw_patrol_lines(size)
        self._draw_preview()

    def _draw_preview(self) -> None:
        for item in self.preview_items:
            self.map_canvas.delete(item)
        self.preview_items = []
        cell = self.preview_cell
        if cell is None or self.tool.get() not in {"object", "navigation"}:
            return
        x, y = cell
        size = self.view_tile_px
        if self.tool.get() == "navigation":
            item = self.map_canvas.create_rectangle(
                x * size, y * size, (x + 1) * size, (y + 1) * size,
                outline="#44aaff", width=2, dash=(4, 2),
            )
            self.preview_items.append(item)
            return

        kind = self.object_kind.get()
        valid = True
        object_x, object_y, object_width, object_height = x, y, 1, 1
        if kind == "door":
            gap = self._find_door_gap(x, y, self.door_orientation.get())
            if gap is None:
                valid = False
                object_width, object_height = (2, 1) if self.door_orientation.get() == "horizontal" else (1, 2)
            else:
                object_x, object_y, object_width, object_height = gap
        elif kind == "window":
            valid = self._can_place_window(x, y)
        elif kind == "decoration":
            asset = self.entity_assets.get(self.entity_asset.get())
            if asset is None:
                valid = False
            else:
                object_width = int(asset.get("width_tiles", 1))
                object_height = int(asset.get("height_tiles", 1))
                valid = x + object_width <= self.width and y + object_height <= self.height

        color = "#2299ff" if valid else "#ee3333"
        item = self.map_canvas.create_rectangle(
            object_x * size,
            object_y * size,
            (object_x + object_width) * size,
            (object_y + object_height) * size,
            outline=color,
            width=3,
            dash=(6, 3),
        )
        label = "설치 가능" if valid else "설치 불가"
        text = self.map_canvas.create_text(
            object_x * size + 4,
            object_y * size + 4,
            text=label,
            fill=color,
            anchor=tk.NW,
        )
        self.preview_items.extend((item, text))

    def _draw_navigation_graph(self, size: int) -> None:
        nodes = {node["node_id"]: node for node in self.nav_nodes}
        for edge in self.nav_edges:
            first = nodes.get(edge["from_id"])
            second = nodes.get(edge["to_id"])
            if first is None or second is None:
                continue
            color = {"preferred": "#22aa66", "normal": "#2277cc", "avoid": "#cc7722"}.get(edge.get("mode"), "#2277cc")
            self.map_canvas.create_line(
                (first["x"] + 0.5) * size,
                (first["y"] + 0.5) * size,
                (second["x"] + 0.5) * size,
                (second["y"] + 0.5) * size,
                fill=color,
                width=3,
            )
        for node in self.nav_nodes:
            x = (node["x"] + 0.5) * size
            y = (node["y"] + 0.5) * size
            selected = node["node_id"] == self.selected_nav_node
            self.map_canvas.create_oval(
                x - 7,
                y - 7,
                x + 7,
                y + 7,
                fill="#ffff44" if selected else "#44aaff",
                outline="#111",
                width=2,
            )

    def _draw_patrol_lines(self, size: int) -> None:
        routes: dict[str, list[dict[str, Any]]] = {}
        for obj in self.objects:
            if obj["kind"] == "patrol_node":
                route_id = str(obj["properties"].get("route_id", "default"))
                routes.setdefault(route_id, []).append(obj)
        for nodes in routes.values():
            nodes.sort(key=lambda item: int(item["properties"].get("order", 0)))
            for first, second in zip(nodes, nodes[1:]):
                self.map_canvas.create_line(
                    (first["x"] + 0.5) * size,
                    (first["y"] + 0.5) * size,
                    (second["x"] + 0.5) * size,
                    (second["y"] + 0.5) * size,
                    fill="#2a4",
                    width=2,
                )

    def load_map(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showerror("맵 열기 실패", str(exc), parent=self)
            return
        self.map_path = path.resolve()
        self.width = int(data.get("width", DEFAULT_WIDTH))
        self.height = int(data.get("height", DEFAULT_HEIGHT))
        size = self.width * self.height
        self.ground = [0] * size
        self.architecture = [0] * size
        self.decoration = [0] * size
        self.collision = [False] * size
        self.occluder = [False] * size
        self.objects = []
        self.nav_nodes = []
        self.nav_edges = []
        self.selected_nav_node = None
        self.wall_edges = []
        self.map_props = _tiled_properties(data.get("properties"))
        self.map_props.setdefault("wall_thickness_m", WALL_THICKNESS_M)
        tilesets = data.get("tilesets", [])
        if tilesets:
            self.load_tileset_reference(path, tilesets[0])
        for layer in data.get("layers", []):
            name = str(layer.get("name", "")).lower()
            if layer.get("type") == "tilelayer":
                values = list(layer.get("data", []))
                if name == "ground":
                    self.ground = (values + [0] * size)[:size]
                elif name in {"architecture", "wall", "walls"}:
                    self.architecture = (values + [0] * size)[:size]
                elif name in {"decoration", "decorations"}:
                    self.decoration = (values + [0] * size)[:size]
                elif name in {"collision", "obstacle"}:
                    self.collision = [bool(value) for value in (values + [0] * size)[:size]]
                elif name in {"occluder", "wall", "vision"}:
                    self.occluder = [bool(value) for value in (values + [0] * size)[:size]]
            elif layer.get("type") == "objectgroup":
                for raw in layer.get("objects", []):
                    kind = str(raw.get("class") or raw.get("type") or "").strip()
                    if not kind:
                        continue
                    properties = _tiled_properties(raw.get("properties"))
                    if kind == "nav_node":
                        self.nav_nodes.append({
                            "node_id": str(properties.get("node_id", raw.get("id"))),
                            "x": int(float(raw.get("x", 0)) // TILE_SIZE),
                            "y": int(float(raw.get("y", 0)) // TILE_SIZE),
                            "kind": str(properties.get("node_kind", "corridor")),
                        })
                        self.next_object_id = max(self.next_object_id, int(raw.get("id", 0)) + 1)
                        continue
                    if kind == "nav_edge":
                        self.nav_edges.append({
                            "from_id": str(properties.get("from_id", "")),
                            "to_id": str(properties.get("to_id", "")),
                            "mode": str(properties.get("mode", "normal")),
                            "cost": float(properties.get("cost", 1.0)),
                            "width_m": float(properties.get("width_m", 1.0)),
                            "one_way": str(properties.get("one_way", "false")).lower() == "true",
                        })
                        self.next_object_id = max(self.next_object_id, int(raw.get("id", 0)) + 1)
                        continue
                    if kind == "wall_edge":
                        self.wall_edges.append({
                            "x": int(properties.get("cell_x", float(raw.get("x", 0)) // TILE_SIZE)),
                            "y": int(properties.get("cell_y", float(raw.get("y", 0)) // TILE_SIZE)),
                            "edge": str(properties.get("edge", "north")),
                            "thickness_m": float(properties.get("thickness_m", WALL_THICKNESS_M)),
                        })
                        self.next_object_id = max(self.next_object_id, int(raw.get("id", 0)) + 1)
                        continue
                    self.objects.append({
                        "id": raw.get("id", self.next_object_id),
                        "kind": kind,
                        "x": int(float(raw.get("x", 0)) // TILE_SIZE),
                        "y": int(float(raw.get("y", 0)) // TILE_SIZE),
                        "width": int(round(float(raw.get("width", 0)) / TILE_SIZE)),
                        "height": int(round(float(raw.get("height", 0)) / TILE_SIZE)),
                        "properties": properties,
                    })
                    self.next_object_id = max(self.next_object_id, int(raw.get("id", 0)) + 1)
        self.refresh_map()
        self.status.set(f"열림: {path.name}")

    def settings_dialog(self) -> None:
        window = tk.Toplevel(self)
        window.title("모듈 설정")
        window.transient(self)
        fields = {
            "module_id": "모듈 ID",
            "module_type": "module/facility",
            "variant_id": "바리에이션 ID",
            "wall_thickness_m": "벽 두께(m)",
            "facility_type": "시설 종류",
            "allowed_region_sizes": "허용 지역 규모(쉼표 구분)",
            "allowed_importance_tiers": "허용 중요도(쉼표 구분)",
            "supports": "시설 기능(쉼표 구분)",
            "has_vehicle_entry": "차량 진입구(true/false)",
            "has_pedestrian_entry": "보행자 출입구(true/false)",
            "attached_watchposts": "부속 감시초소 수",
        }
        variables: dict[str, tk.StringVar] = {}
        for row, (key, label) in enumerate(fields.items()):
            ttk.Label(window, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=3)
            variable = tk.StringVar(value=str(self.map_props.get(key, "")))
            variables[key] = variable
            ttk.Entry(window, textvariable=variable, width=42).grid(row=row, column=1, padx=6, pady=3)

        def save() -> None:
            for key, variable in variables.items():
                value: Any = variable.get()
                if key in {"has_vehicle_entry", "has_pedestrian_entry"}:
                    value = value.lower() in {"true", "1", "yes", "on"}
                elif key == "attached_watchposts":
                    try:
                        value = int(value or 0)
                    except ValueError:
                        value = 0
                elif key == "wall_thickness_m":
                    try:
                        value = float(value or WALL_THICKNESS_M)
                    except ValueError:
                        value = WALL_THICKNESS_M
                elif key in {"allowed_region_sizes", "allowed_importance_tiers", "supports"}:
                    value = [item.strip() for item in value.split(",") if item.strip()]
                self.map_props[key] = value
            window.destroy()
            self.status.set("모듈 설정 저장됨")

        ttk.Button(window, text="적용", command=save).grid(row=len(fields), column=1, sticky="e", padx=6, pady=8)

    def _map_properties_for_tiled(self) -> dict[str, Any]:
        result = dict(self.map_props)
        for key in {"allowed_region_sizes", "allowed_importance_tiers", "supports"}:
            if isinstance(result.get(key), list):
                result[key] = json.dumps(result[key], ensure_ascii=False)
        return result

    def to_tiled(self) -> dict[str, Any]:
        tile_count = len(self.tiles)
        tileset_image = ""
        if self.tileset_path and self.map_path:
            tileset_image = os.path.relpath(self.tileset_path, self.map_path.parent).replace("\\", "/")
        elif self.tileset_path:
            tileset_image = self.tileset_path.name
        layers: list[dict[str, Any]] = []
        for layer_id, (name, values, visible) in enumerate([
            ("Ground", self.ground, True),
            ("Architecture", self.architecture, True),
            ("Decoration", self.decoration, True),
            ("Collision", [self.architecture[i] if self.collision[i] else 0 for i in range(len(self.collision))], False),
            ("Occluder", [self.architecture[i] if self.occluder[i] else 0 for i in range(len(self.occluder))], False),
        ], start=1):
            layers.append({
                "data": values,
                "height": self.height,
                "id": layer_id,
                "name": name,
                "opacity": 1,
                "type": "tilelayer",
                "visible": visible,
                "width": self.width,
                "x": 0,
                "y": 0,
            })
        object_layers = {"Objects": [], "AI": []}
        for obj in self.objects:
            if obj["kind"] in {"enemy_spawn", "patrol_node", "guard_point", "cover_point"}:
                layer_name = "AI"
            elif obj["kind"] == "decoration":
                layer_name = "DecorationObjects"
            else:
                layer_name = "Objects"
            point = obj["width"] == 0 and obj["height"] == 0
            raw: dict[str, Any] = {
                "class": obj["kind"],
                "id": obj["id"],
                "name": obj["kind"],
                "properties": _property_list(obj["properties"]),
                "type": "",
                "x": obj["x"] * TILE_SIZE + (TILE_SIZE / 2 if point else 0),
                "y": obj["y"] * TILE_SIZE + (TILE_SIZE / 2 if point else 0),
            }
            if point:
                raw["point"] = True
            else:
                raw["width"] = obj["width"] * TILE_SIZE
                raw["height"] = obj["height"] * TILE_SIZE
            object_layers[layer_name].append(raw)
        nav_node_objects = []
        node_positions = {node["node_id"]: node for node in self.nav_nodes}
        next_generated_id = self.next_object_id
        for node in self.nav_nodes:
            nav_node_objects.append({
                "class": "nav_node",
                "id": next_generated_id,
                "name": node["node_id"],
                "point": True,
                "properties": _property_list({
                    "node_id": node["node_id"],
                    "node_kind": node.get("kind", "corridor"),
                }),
                "type": "",
                "x": (node["x"] + 0.5) * TILE_SIZE,
                "y": (node["y"] + 0.5) * TILE_SIZE,
            })
            next_generated_id += 1
        nav_edge_objects = []
        for edge in self.nav_edges:
            start = node_positions.get(edge["from_id"])
            if start is None:
                continue
            nav_edge_objects.append({
                "class": "nav_edge",
                "id": next_generated_id,
                "name": "nav_edge",
                "point": True,
                "properties": _property_list(edge),
                "type": "",
                "x": (start["x"] + 0.5) * TILE_SIZE,
                "y": (start["y"] + 0.5) * TILE_SIZE,
            })
            next_generated_id += 1
        if nav_node_objects:
            layers.append({
                "draworder": "topdown",
                "id": len(layers) + 1,
                "name": "Navigation",
                "objects": nav_node_objects,
                "opacity": 1,
                "type": "objectgroup",
                "visible": True,
                "x": 0,
                "y": 0,
            })
        if nav_edge_objects:
            layers.append({
                "draworder": "topdown",
                "id": len(layers) + 1,
                "name": "NavigationEdges",
                "objects": nav_edge_objects,
                "opacity": 1,
                "type": "objectgroup",
                "visible": True,
                "x": 0,
                "y": 0,
            })
        wall_edge_objects = []
        for index, edge in enumerate(self.wall_edges, start=self.next_object_id):
            wall_edge_objects.append({
                "class": "wall_edge",
                "id": index,
                "name": "wall_edge",
                "point": True,
                "properties": _property_list({
                    "cell_x": edge["x"],
                    "cell_y": edge["y"],
                    "edge": edge["edge"],
                    "thickness_m": edge.get("thickness_m", 0.25),
                }),
                "type": "",
                "x": edge["x"] * TILE_SIZE,
                "y": edge["y"] * TILE_SIZE,
            })
        if wall_edge_objects:
            layers.append({
                "draworder": "topdown",
                "id": len(layers) + 1,
                "name": "WallEdges",
                "objects": wall_edge_objects,
                "opacity": 1,
                "type": "objectgroup",
                "visible": True,
                "x": 0,
                "y": 0,
            })
        for name, objects in object_layers.items():
            layers.append({
                "draworder": "topdown",
                "id": len(layers) + 1,
                "name": name,
                "objects": objects,
                "opacity": 1,
                "type": "objectgroup",
                "visible": True,
                "x": 0,
                "y": 0,
            })
        document: dict[str, Any] = {
            "class": "",
            "height": self.height,
            "infinite": False,
            "layers": layers,
            "nextlayerid": len(layers) + 1,
            "nextobjectid": next_generated_id + len(self.wall_edges),
            "orientation": "orthogonal",
            "properties": _property_list(self._map_properties_for_tiled()),
            "renderorder": "right-down",
            "tileheight": TILE_SIZE,
            "tilewidth": TILE_SIZE,
            "tilesets": [],
            "type": "map",
            "version": "1.10",
            "width": self.width,
        }
        if self.tileset_path and tile_count:
            columns = self.tileset_image.width // TILE_SIZE if self.tileset_image else 1
            rows = self.tileset_image.height // TILE_SIZE if self.tileset_image else 1
            document["tilesets"] = [{
                "columns": columns,
                "firstgid": 1,
                "image": tileset_image,
                "imageheight": rows * TILE_SIZE,
                "imagewidth": columns * TILE_SIZE,
                "margin": 0,
                "name": self.tileset_path.stem,
                "spacing": 0,
                "tilecount": columns * rows,
                "tileheight": TILE_SIZE,
                "tilewidth": TILE_SIZE,
            }]
        return document

    def save_map_dialog(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".tmj",
            filetypes=[("Tiled JSON map", "*.tmj"), ("All files", "*.*")],
        )
        if path:
            self.save_map(Path(path))

    def save_map(self, path: Path) -> None:
        self.map_path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_tiled(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.status.set(f"저장됨: {path.name}")

    def compile_current(self) -> None:
        if self.map_path is None:
            self.save_map_dialog()
        if self.map_path is None:
            return
        self.save_map(self.map_path)
        try:
            runtime = compile_module(self.map_path)
            output = self.map_path.parent / "compiled" / self.map_path.stem
            runtime_path = output / f"{self.map_path.stem}.runtime.json"
            write_runtime(runtime, runtime_path)
            write_ai_package(runtime, output / "ai_package", source=self.map_path)
        except Exception as exc:  # keep errors visible without killing the editor
            messagebox.showerror("컴파일 실패", str(exc), parent=self)
            return
        self.status.set(f"컴파일됨: {output}")


def launch(map_path: Path | None = None) -> None:
    app = MapEditor(map_path)
    app.mainloop()
