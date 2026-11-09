# Revenant: L.O.S.T Map Tool

The first map pipeline uses **Tiled Map Editor** for hand-authored modules and
facilities. The Python compiler converts each `.tmj` module into:

- runtime JSON for navigation, AI routes, tactical points, doors, and enemy spawns;
- an AI-review package containing a readable manifest and an SVG layout preview.

The tool does not create the complete world. The procedural world generator will
choose and connect these modules later.

## Installation

- Python 3.12+
- Tiled 1.12+

The compiler uses Python's standard library and Pillow for the AI-review PNG.
Install dependencies with:

```powershell
python -m pip install -r maptool/requirements.txt
```

For the project environment on Windows:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r maptool\requirements.txt
```

## Compile a module

From the repository root:

```powershell
& .\.venv\Scripts\python.exe -m maptool compile path\to\module.tmj --output build\module
```

If `python` is not on `PATH`, use the installed Python executable directly:

```powershell
& .\.venv\Scripts\python.exe -m maptool compile path\to\module.tmj
```

The output contains `<module>.runtime.json` and `ai_package/`.

The AI package contains both `layout.png` for multimodal models and
`layout.svg` for lossless inspection. Refer to `AI_TILESET_GUIDE.md` for the
separate structural/decorative tile prompts and the browser upload list.

## Create a new module template

```powershell
& .\.venv\Scripts\python.exe -m maptool init hq_01 --type facility --output maptool_workspace
```

For a facility with pool constraints:

```powershell
& .\.venv\Scripts\python.exe -m maptool init hq_01 --type facility `
  --facility-type hq `
  --region-sizes medium,large,extra_large `
  --importance-tiers important,core `
  --supports command,communication,garrison `
  --vehicle-entry `
  --watchposts 2
```

This creates the standard `Ground`, `Architecture`, `Decoration`, `Collision`,
`Occluder`, `Objects`, and `AI` layers. It does not include art; add the
project's tileset in the editor.

## Use the module editor

The task-focused editor avoids direct Tiled editing for the common workflow:

```powershell
& .\.venv\Scripts\python.exe -m maptool edit
```

The editor has separate `Ground`, `Wall`, and `Decoration` painting modes.
Painting a wall places a selected 32px wall tile and a `wall_edge` record. The
default wall thickness is 0.375m (24px), so the compiler creates a thick
collision/occlusion rectangle along the selected grid edge instead of blocking
the whole floor cell. Decoration tiles are visual-only and never enter the
collision, occlusion, or navigation data. The object mode places enemy spawns,
patrol nodes, doors, stairs, connectors, guards, and authored cover points.
Right-click an object cell to remove the object.

To edit a tilemap made in Tiled:

```powershell
& .\.venv\Scripts\python.exe -m maptool edit path\to\module.tmj
```

The editor imports the `Ground`, `Architecture`, and `Decoration` layers and
the first embedded or external Tiled tileset. It adds semantic `WallEdges`,
`Objects`, and `AI` data, then saves a Tiled-compatible `.tmj` file.

## Tiled conventions

Use the following object classes (Tiled calls these object types/classes):

| Class | Purpose |
| --- | --- |
| `connector` | A road or pedestrian connection point |
| `enemy_spawn` | Enemy spawn point; player spawns are not exported |
| `patrol_node` | A node in a hand-authored patrol route |
| `guard_point` | Static guard position |
| `cover_point` | Cover position and facing |
| `door` | Dynamic door and movement/vision/sound properties |
| `window` | Window/opening properties |
| `stairs` | Floor transition |
| `wall_edge` | Thick wall segment on a grid edge |
| `wall`, `occluder`, `obstacle` | Static geometry objects |

Tile layers named `Collision`, `Obstacle`, `Occluder`, `Wall`, or `Vision` are
also compiled. Non-empty tiles in collision/obstacle layers become merged
collision rectangles. Non-empty tiles in occluder/wall/vision layers become
merged sight-blocking rectangles.

## Useful object properties

All properties are preserved in the runtime JSON. Common properties include:

- `module_id`, `module_type`, `variant_id`: map properties;
- `route_id`, `order`, `loop`, `wait_seconds`, `facing_degrees`: patrol nodes;
- `group_id`, `archetype`, `count`, `initial_state`: enemy spawns;
- `quality`, `cover_height`, `facing_degrees`: authored cover points;
- `connector_type`, `connector_width`, `direction`: connectors;
- `blocks_movement`, `blocks_vision`, `blocks_sound`: door/window closed-state defaults;
- `sound_transmission_closed`, `sound_transmission_open`, `sound_transmission_broken`: door/window sound transmission from `0.0` to `1.0`.

The compiler creates a local 8-way navigation grid from `Collision` tiles (or
from the inverse of a `Walkable`/`Navigation` layer when one is present). This
is a runtime pathfinding input, not a reachability validator. Doors and stairs
are exported as dynamic navigation portals.

The current scale is 32px tiles at 0.5m each, while the world coordinate scale
remains 64px per meter. Wall edges are 0.375m thick and are stored separately
from whole-cell collision tiles.

Search, flank, and retreat destinations are not authored map objects. The game
AI derives them at runtime from the navigation grid, visibility blockers,
available cover, squad state, and the last known player information.

Facility maps also export a `facility` selection block. It records the facility
type, allowed region-size tiers, allowed strategic-importance tiers, campaign
capabilities, entry types, and attached watchpost count. The future world
generator uses this block to select a valid prefab variant; it does not change
the authored facility layout.

## Design boundary

The compiler intentionally does not decide whether a module is fun or
reachable. It normalizes author-authored data and compiles static tile layers
into larger geometry primitives so the game does not need to scan every wall
tile for every AI query.
