# Project notes for Claude

This file captures the *non-obvious* things about how `blender_to_svg.py`
works — the design decisions, edge cases, and historical hard-won fixes
that you can't easily reconstruct from reading the code.

Read `README.md` for the user-facing overview and CLI surface. This file
is about the internals.

## What we're building

A Blender-to-SVG exporter. The user's Blender scene gets rendered through
the active camera as a stack of flat-shaded or Lambert-shaded SVG polygons
with optional edge strokes. The output is intended to be editable in any
vector tool, not rasterised.

The entry point is `blender_to_svg.sh` which runs Blender headlessly with
`blender_to_svg.py` as the script and forwards CLI flags via `--`.

## Pipeline overview

`main()` in `blender_to_svg.py`:

1. Find the camera, derive width/height from `scene.render`.
2. Pick lights: prefer scene `SUN` objects; fall back to viewport solid
   lights; fall back to a single default key light. Only used in
   `lambert` mode.
3. For each visible mesh:
   - Evaluate with modifiers (`obj.evaluated_get(depsgraph)`).
   - Compute world-space vertex positions and per-poly world normals.
   - Backface-cull (`normal · view_dir <= 0`).
   - Project verts via `world_to_camera_view` to NDC, then to screen
     coords (flipping Y to put origin at top-left).
   - **Screen-space dedup** of polygons (see "Dedup" below).
   - Classify each visible polygon's edges (boundary / silhouette /
     crease) and store in `edge_kept`.
   - In `flat` mode, union-find polygons connected via *non-kept* edges
     into components, then either emit one `<path>` per multi-poly
     component (if chaining succeeds) or fall back to per-polygon.
   - In `lambert` mode, emit per-polygon directly.
4. Sort meshes by their average polygon depth (painter's algorithm at
   mesh level) and emit polygons-then-edges per mesh group.

## Per-mesh batching (not per-polygon)

Within a mesh: all polygon fills are emitted first, then all of that
mesh's stroked edges. This is *deliberate* — it was an explicit fix for
the sphere case where a near polygon's same-colour seam-mask stroke
(0.6 px) would eat into a far silhouette polygon's outline at the apex
where silhouette polys collapse to sub-pixel width.

The tradeoff: within a single mesh, polygon-depth ordering of edges is
lost — all the mesh's edges go on top of all its fills. For backface-
culled mostly-convex meshes this is invisible. For a heavily self-
occluding single mesh, interior crease lines of the rear part could show
through the front part of the same mesh. We've decided that's acceptable.

Between meshes, painter's order still applies, so a closer mesh's fills
still correctly cover a farther mesh's edges.

**Don't switch this to per-polygon ordering** without re-testing the
sphere apex silhouette. We tried it during the megaxe debugging and it
regressed the sphere.

## Edge classification (`edge_kept`)

For each visible polygon, each edge is classified once into a boolean:

- **Boundary**: only this polygon adjacent to the edge → drawn.
- **Silhouette**: a neighbour exists but is *not visible* (back-facing,
  clipped, or deduped out) → drawn.
- **Crease**: both neighbours visible and the dihedral angle between
  this polygon and the neighbour's world normal is `>= --crease-angle`
  → drawn.
- **Interior**: otherwise → not drawn.

The classifier explicitly uses `poly_visible[n]` (not just
`poly_front[n]`). This matters: when dedup removes the inner shell of a
solidify-style mesh, the visible outer-shell polygons' shared-with-inner
edges become silhouette boundaries.

## Coincident-face dedup

The model can have multiple polygons at (nearly) the same screen
position — typically from solidify modifiers or duplicated/joined meshes.
We collapse those into a single render: a frozenset of
`(round(x, 1), round(y, 1))` over each polygon's projected vertices is the
key. If two visible polygons share a key, the one with the smaller depth
(closer to camera) wins; the other gets `poly_visible[i] = False`.

**Tolerance was deliberately chosen at 0.1 user units**: 5 decimals
missed the megaxe case because the inner shell vertices were ~0.03 user
units off from the outer shell. 0.1 catches that without merging
genuinely distinct adjacent polygons (any visible polygon spans much more
than 0.1 user units in a 100×100-ish canvas).

This dedup is what made the X-pattern artifact go away on `megaxe.blend`.
It is necessary and load-bearing — don't remove it.

## Flat-mode component merging

In `flat` mode, all visible same-material polygons within a mesh merge
into one component — the user expects one editable shape per coloured
region. Creases between those polys are still drawn, but as separate
overlay lines on top of the merged shape, not as part of the closed
outline.

Union-find runs on visible polygons with two criteria:

1. **3D-adjacent same-material** (any edge, kept or non-kept). This is
   the change from the original "non-kept edges only" rule — it lets
   adjacent faces with a sharp 3D crease still belong to the same
   single SVG shape.
2. **2D-coincident kept edge from different 3D mesh edges**: same as
   before — handles solidify-boundary silhouettes and joined-sub-mesh
   seams meeting visually.

Each kept edge in flat mode is then classified by looking at its
`(material, rounded-2D-key)` bucket:

| bucket population              | category    | rendered as                              |
| ------------------------------ | ----------- | ---------------------------------------- |
| singleton                      | outline     | part of the chained closed path          |
| ≥ 2 entries, all same `ei`     | interior    | overlay `<line>` on top of the path      |
| ≥ 2 entries, mixed `ei`        | cancelled   | not drawn at all                         |

`ei` is the mesh edge index — same `ei` across multiple entries means
the same 3D edge shared by adjacent faces (a true crease); mixed `ei`
means two distinct mesh edges that happen to project to the same 2D line
(a visual seam between separate sub-meshes).

Outline edges feed `chain_segments` to build one closed `<path>` per
component. Interior crease edges are deduped by `ei` (each shared
mesh edge appears in two polys' kept sets) and pushed to `edges_out`
so they emit as `<line>` elements after the path. Cancelled edges drop
out.

For size-1 components and lambert mode we keep the older "polygon with
its own black stroke" or "polygon + per-edge `<line>`" emission.

### `chain_segments` uses 2D position IDs, not mesh vertex indices

This matters specifically for the 2D-coincident-seam case: poly A and
poly B may have the *same* 2D corner point but *different* mesh vertex
indices there (separate sub-meshes, joined object). If chain_segments
used mesh indices for connectivity, the chain couldn't walk across the
seam. So we intern rounded 2D positions into integer IDs locally per
component and feed those to chain_segments instead.

### Why chain_segments has a clean/dirty return

`chain_segments` walks edges and stitches them into closed cycles by
picking the first unused incident segment at each vertex. For a clean
manifold component (every vertex has degree 2 in the kept-edge graph),
this produces one closed loop per outline component. Perfect.

When it can't close — dedup orphaned an edge, the topology has a
T-junction, or cancellation removed an edge that didn't have a clean
counterpart — the function returns `is_clean = False`. The caller throws
away the chain output and falls back to a **compact two-path emission**:

- One `<path>` whose `d` lists every polygon in the component as a
  Z-closed subpath, painted with `fill="X" stroke="X" stroke-width="0.6"`
  for seam masking.
- One `<path>` whose `d` lists every non-cancelled kept edge as an
  unclosed `M…L…` subpath, painted with `fill="none" stroke="#000"
  stroke-width="{sw}"`.

So a 30-polygon failing component costs 2 SVG elements, not 30+30.
Visually identical to per-polygon emission, drastically smaller files.
This was the fix that took `megaxe.blend` from ~22 KB / 200 elements down
to ~14 KB / 80 elements.

The sphere case still hits the fast single-path branch; only the
pathologically-topologied components hit this fallback.

## Why 0.6 px same-colour stroke on polygons

Adjacent same-coloured `<polygon>` elements often show thin
anti-aliasing seams in Inkscape/Illustrator (browsers usually handle
this fine). To paper over those, polygons that don't use their own
black stroke (i.e. `all_edges_kept == False`) get a 0.6 px stroke in
their own fill colour. This is invisible against the fill but adds 0.3
px of coverage on each side, closing AA seams.

This caused the **sphere apex silhouette thinning** at one point — a
near polygon's 0.3 px halo was wide enough to overrun a sub-pixel-wide
far polygon and cover its silhouette line. That's why per-mesh
batching exists (see above): emitting all the mesh's silhouette lines
*after* all its fills means they're never inside the halo region of
later fills.

If you tweak this stroke width, re-test both:
- The sphere with `-c 30` (apex outline should not thin).
- Densely-tiled flat-shaded meshes (seams should stay invisible).

## Coordinate spaces in the code

- **Local mesh coords**: `mesh.vertices[i].co`.
- **World**: `world_verts[i] = world_matrix @ co`.
- **Camera/NDC**: `world_to_camera_view(scene, camera, world)` returns a
  `Vector` where `.x` and `.y` are in `[0, 1]` for points inside the
  camera frame and `.z` is the *distance along the camera's view
  direction* — positive for points in front of the camera. We skip any
  polygon with a `z <= 0` vertex (no real near-plane clipping).
- **Screen**: `(.x * width, (1 - .y) * height, .z)`. Y is flipped so SVG
  origin is top-left.

`poly_depth[i]` is the average screen-space `z` of the polygon's
vertices — the painter's-algorithm sort key.

## Lighting details

`get_sun_lights(scene)` reads each enabled, visible `SUN`. A sun's
shining direction is its local `-Z` in world space, so the direction
*toward* the light from a surface is the world `+Z` axis of its
`matrix_world.to_3x3()`. The light intensity used in shading is
`color × energy`, and there's no falloff (suns are directional in
Blender).

If no sun is found, `get_viewport_lights(camera)` reads
`bpy.context.preferences.system.solid_lights`. Each light's `direction`
is in *view space*, so it's rotated by the active 3D viewport's
`studiolight_rotate_z` (if any) and then transformed to world space by
the camera's rotation matrix. In `--background` Blender there's no
3D viewport, so the rotation is 0.

`shade_lambert(normal, base, lights, ambient=0.05)` computes
`ambient × base + Σ base × light_colour × max(0, n · d)` with no
clamping until the final cap at 1.0 per channel.

## Things that look wrong but aren't

- `mesh.use_nodes` is deprecated in Blender 6.0; a DeprecationWarning
  prints during export. Harmless until 6.0 actually removes it.
- Many faces appearing "twice in a row" in the SVG before dedup ran was
  the megaxe symptom; it's now collapsed.
- Lambert-shaded polygons in `-c 0` mode all use their own polygon
  stroke (no `<line>` elements), giving zero `<line>` count. That's the
  intended optimization.

## When testing changes

The two scenes that catch most issues:

- **`simple.blend`** — sphere + two wedges. Tests the chain-segment
  fast path, flat-mode merging, multi-material rendering, and the
  sphere-apex silhouette case.
- **`megaxe.blend`** — multi-material single-object mesh with
  near-coincident faces. Tests dedup and the chain_segments fallback.

Spot-check rasterisation:

```bash
./blender_to_svg.sh scene.blend /tmp/out.svg -c 30 -s flat
qlmanage -t -s 2400 -o /tmp/ /tmp/out.svg
open /tmp/out.svg.png
```

For zoom-in inspection of a specific region:

```bash
sips -c <h> <w> --cropOffset <y> <x> /tmp/out.svg.png --out /tmp/zoom.png
```

(macOS only.) On Linux, use `rsvg-convert` and ImageMagick `convert -crop`.
