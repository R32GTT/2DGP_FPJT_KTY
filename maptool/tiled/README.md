# Tiled setup

Create an orthogonal map for each reusable road, terrain, or facility module.
Set the map tile size to the project baseline (`64x64` pixels for a 1m tile)
unless a deliberate asset scale requires another value.

Recommended layers:

```text
Ground          visual ground tiles
Architecture    visual walls/buildings
Collision       non-empty tiles block movement
Occluder        non-empty tiles block sight
Objects         doors, windows, stairs, connectors, and markers
AI              enemy spawns, patrol nodes, cover, guard points
```

For a first pass, object classes can be entered in Tiled's object properties.
The compiler accepts both the current `class` field and Tiled's older `type`
field.

The module map itself is not the campaign world. A later generator will choose
module variants and connect their `connector` objects.
