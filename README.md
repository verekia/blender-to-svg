# blender-to-svg

Export a Blender scene to an SVG that looks like the scene as seen through its
active camera — flat-shaded or Lambert-shaded vector art rather than a raster
render.

Each visible polygon becomes an SVG `<polygon>` (or part of a `<path>`).
Faces are backface-culled, painter's-sorted by depth, and stroked with their
edges. The result is a self-contained, editable SVG that opens cleanly in
Inkscape, Illustrator, browsers, or any other vector tool.

## Requirements

- macOS, Linux, or Windows
- Blender 3.x or newer on `PATH`, or installed at the default macOS location
  (`/Applications/Blender.app/Contents/MacOS/Blender`). Override with the
  `BLENDER` environment variable.

The Python script runs *inside* Blender (it uses `bpy`, `bmesh`,
`bpy_extras.object_utils.world_to_camera_view`, and `mathutils`), so you don't
need to install any Python packages yourself.

## Usage

```bash
./blender_to_svg.sh <file.blend> [output.svg] [flags...]
```

If `output.svg` is omitted, the script writes alongside the `.blend` with the
same base name (e.g. `scene.blend` → `scene.svg`).

You can also invoke Blender directly if you prefer:

```bash
blender -b file.blend --python blender_to_svg.py -- [output.svg] [flags...]
```

### Examples

```bash
./blender_to_svg.sh scene.blend                       # Lambert shading, all edges
./blender_to_svg.sh scene.blend out.svg               # custom output path
./blender_to_svg.sh scene.blend -w 2                  # thicker outlines
./blender_to_svg.sh scene.blend -c 30                 # only sharp creases + silhouettes
./blender_to_svg.sh scene.blend -c 180                # outline only, no internal lines
./blender_to_svg.sh scene.blend -s flat               # raw material colours, no shading
./blender_to_svg.sh scene.blend -s flat -c 30 -w 0.5  # merged same-colour regions
```

### Flags

| Flag                            | Default     | Description                                                                                                       |
| ------------------------------- | ----------- | ----------------------------------------------------------------------------------------------------------------- |
| `-w, --stroke-width FLOAT`      | `1.0`       | Outline thickness in SVG user units.                                                                              |
| `-c, --crease-angle DEGREES`    | `0`         | Minimum dihedral angle for an *interior* edge to be drawn. `0` = every edge; `180` = silhouette only; `30` keeps cube/wedge corners but skips smooth surfaces like a sphere's quads. |
| `-s, --shading {lambert,flat}`  | `lambert`   | `lambert` shades each face with `Σ base × light_colour × max(0, n·d)`; `flat` uses the raw material colour with no shading. |

Silhouette edges (a face whose neighbour is back-facing or absent) and open
mesh-boundary edges are *always* drawn, regardless of `--crease-angle`.

## How shading works

In `lambert` mode the renderer looks for light sources, in this order of
preference:

1. **`SUN` lights in the scene** — each enabled, visible sun contributes
   `colour × energy` along its world `+Z` axis (Blender suns shine along
   their local `-Z`).
2. **Blender's viewport "Solid" lights** — taken from
   `Preferences → Lights → Studio`, rotated by the active 3D viewport's
   `studiolight_rotate_z` if one is open, then transformed from view space
   to world space using the camera's orientation.
3. **A single default key light** if nothing above is available.

In `flat` mode, no lights are read. Each face's fill is the material's base
colour (Principled BSDF `Base Color`, then `EMISSION Color`, then the legacy
`diffuse_color`, then `#cccccc` if the polygon has no material slot). The
colour is converted from linear to sRGB before being written as `#rrggbb`.

## What's in the scene

- **Camera** — the scene's active camera, or the first `CAMERA` object if
  none is active. Errors out if there is no camera.
- **Mesh objects** — every visible mesh contributes polygons. The script
  evaluates each mesh with modifiers applied (`obj.evaluated_get(depsgraph)`).
- **Lights** — only `SUN` lights are read in Lambert mode; point/spot/area
  lights are ignored.
- **Output resolution** — comes from
  `scene.render.resolution_x/y × resolution_percentage`. The SVG `viewBox`
  matches these dimensions; coordinates are in those user units (no
  scaling — set the resolution in Blender to control density of details).

## Limitations

- **Painter's algorithm**, not a Z-buffer. Polygons that interpenetrate or
  whose centres don't reflect their actual visibility order may render in
  the wrong sequence between meshes.
- **No partial near-plane clipping.** A polygon with *any* vertex behind the
  camera is dropped entirely rather than clipped against the near plane.
- **Flat-mode component merging** requires the visible region of a single
  material to have a topologically clean outline. When the topology is
  pathological (open chains from coincident-face dedup, T-junctions,
  non-manifold edges), that component falls back to per-polygon emission —
  still correct, just larger SVG.
- **Coincident face dedup** runs at a 0.1 user-unit screen tolerance. Two
  visible polygons whose rounded 2D vertex sets match are treated as
  duplicates, keeping the closest. Densely-tiled small polygons (tinier than
  ~0.1 user units across) could be over-merged.

## Files

- `blender_to_svg.py` — the export script that runs inside Blender.
- `blender_to_svg.sh` — thin wrapper that invokes Blender headlessly
  (`--background --python …`) and forwards all flags through `--`.
- `simple.blend`, `megaxe.blend`, etc. — sample scenes used during
  development.
