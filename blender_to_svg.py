import bpy
import sys
import os
import math
import argparse
from collections import defaultdict
from mathutils import Vector, Matrix
from bpy_extras.object_utils import world_to_camera_view


DEFAULT_BASE = (0.8, 0.8, 0.8)


def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    parser = argparse.ArgumentParser(prog="blender_to_svg")
    parser.add_argument("output", nargs="?", default=None,
                        help="Output .svg path (defaults to <blend>.svg)")
    parser.add_argument("-w", "--stroke-width", type=float, default=1.0,
                        help="Outline stroke width in SVG user units (default 1.0)")
    parser.add_argument("-c", "--crease-angle", type=float, default=0.0,
                        help="Minimum dihedral angle (degrees) for an interior edge "
                             "to be drawn. 0 = all edges; 180 = outline only; "
                             "e.g. 30 keeps sharp creases but skips smooth surfaces.")
    parser.add_argument("-s", "--shading", choices=("lambert", "flat"),
                        default="lambert",
                        help="Shading mode: 'lambert' (default) shades faces using "
                             "viewport solid lights; 'flat' uses the raw material "
                             "color with no shading.")
    return parser.parse_args(argv)


def linear_to_srgb_byte(x):
    x = max(0.0, min(1.0, x))
    if x <= 0.0031308:
        s = 12.92 * x
    else:
        s = 1.055 * (x ** (1.0 / 2.4)) - 0.055
    return int(round(s * 255))


def rgb_to_hex(rgb):
    r, g, b = (linear_to_srgb_byte(c) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def material_base_color(obj, poly):
    if not obj.material_slots:
        return DEFAULT_BASE
    idx = poly.material_index
    if idx >= len(obj.material_slots):
        idx = 0
    mat = obj.material_slots[idx].material
    if mat is None:
        return DEFAULT_BASE
    if mat.use_nodes and mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                base = node.inputs.get("Base Color")
                if base is not None:
                    c = base.default_value
                    return (c[0], c[1], c[2])
            if node.type == 'EMISSION':
                col = node.inputs.get("Color")
                if col is not None:
                    c = col.default_value
                    return (c[0], c[1], c[2])
    c = mat.diffuse_color
    return (c[0], c[1], c[2])


def get_sun_lights(scene):
    """Return list of (world_direction_toward_light, color_rgb) for SUN lights.

    A sun's shining direction is its local -Z in world space; the direction
    from a surface toward the sun is the opposite (the light's world +Z axis).
    """
    lights = []
    for obj in scene.objects:
        if obj.type != 'LIGHT' or obj.data is None:
            continue
        if obj.data.type != 'SUN':
            continue
        if not obj.visible_get():
            continue
        d_world = (obj.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
        col = obj.data.color
        energy = float(getattr(obj.data, "energy", 1.0))
        lights.append((d_world, Vector((col[0] * energy, col[1] * energy, col[2] * energy))))
    return lights


def get_viewport_lights(camera):
    """Return list of (world_direction_toward_light, diffuse_color_rgb).

    Uses Blender's solid-mode viewport lights from user preferences. Their
    directions are stored in view space; we rotate them by the active 3D
    viewport's studio-light Z rotation (if any), then transform to world
    space using the camera's orientation.
    """
    prefs = bpy.context.preferences.system
    rot_z = 0.0
    try:
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        rot_z = space.shading.studiolight_rotate_z
                        break
                break
    except (AttributeError, RuntimeError):
        rot_z = 0.0

    z_rot = Matrix.Rotation(rot_z, 3, 'Z')
    cam_rot = camera.matrix_world.to_3x3()

    lights = []
    for sl in prefs.solid_lights:
        if not sl.use:
            continue
        d_view = Vector(sl.direction)
        if d_view.length == 0.0:
            continue
        d_view = (z_rot @ d_view).normalized()
        d_world = (cam_rot @ d_view).normalized()
        col = sl.diffuse_color
        lights.append((d_world, Vector((col[0], col[1], col[2]))))

    if not lights:
        d = (cam_rot @ Vector((0.3, 0.3, 1.0)).normalized())
        lights.append((d, Vector((1.0, 1.0, 1.0))))
    return lights


def shade_lambert(normal, base, lights, ambient=0.05):
    r, g, b = base
    R = ambient * r
    G = ambient * g
    B = ambient * b
    for d, col in lights:
        nd = normal.dot(d)
        if nd <= 0.0:
            continue
        R += r * col.x * nd
        G += g * col.y * nd
        B += b * col.z * nd
    return (min(R, 1.0), min(G, 1.0), min(B, 1.0))


def chain_segments(segments):
    """Chain undirected segments into closed loops of 2D points.

    Returns (loops, is_clean). is_clean is False if any chain hit a dead end
    before closing — meaning the input has open ends and the loops shouldn't
    be filled.
    """
    if not segments:
        return [], True
    incidence = defaultdict(list)
    for i, seg in enumerate(segments):
        incidence[seg[0]].append(i)
        incidence[seg[1]].append(i)
    used = [False] * len(segments)
    loops = []
    is_clean = True
    for start in range(len(segments)):
        if used[start]:
            continue
        used[start] = True
        va, vb, pa, pb = segments[start]
        loop = [pa, pb]
        start_v = va
        current_v = vb
        while current_v != start_v:
            nxt = None
            for s_idx in incidence[current_v]:
                if not used[s_idx]:
                    nxt = s_idx
                    break
            if nxt is None:
                is_clean = False
                break
            used[nxt] = True
            sva, svb, spa, spb = segments[nxt]
            if sva == current_v:
                next_v, next_p = svb, spb
            else:
                next_v, next_p = sva, spa
            if next_v == start_v:
                break
            loop.append(next_p)
            current_v = next_v
        loops.append(loop)
    return loops, is_clean


def find_camera(scene):
    if scene.camera is not None:
        return scene.camera
    for obj in scene.objects:
        if obj.type == 'CAMERA':
            return obj
    return None


def main():
    args = parse_args()
    output_path = args.output
    stroke_width = args.stroke_width
    crease_threshold = math.radians(args.crease_angle)
    shading_mode = args.shading

    scene = bpy.context.scene
    camera = find_camera(scene)
    if camera is None:
        print("ERROR: no camera found in scene", file=sys.stderr)
        sys.exit(1)

    rscale = scene.render.resolution_percentage / 100.0
    width = int(scene.render.resolution_x * rscale)
    height = int(scene.render.resolution_y * rscale)

    depsgraph = bpy.context.evaluated_depsgraph_get()
    cam_loc = camera.matrix_world.translation
    if shading_mode == "lambert":
        sun_lights = get_sun_lights(scene)
        lights = sun_lights if sun_lights else get_viewport_lights(camera)
    else:
        lights = []

    mesh_groups = []

    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        if not obj.visible_get():
            continue

        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        world_matrix = eval_obj.matrix_world
        normal_matrix = world_matrix.to_3x3().inverted_safe().transposed()

        world_verts = [world_matrix @ v.co for v in mesh.vertices]
        cam_verts = [world_to_camera_view(scene, camera, wv) for wv in world_verts]
        screen_verts = [
            (ndc.x * width, (1.0 - ndc.y) * height, ndc.z) for ndc in cam_verts
        ]

        poly_normal_world = []
        poly_is_front = []
        for poly in mesh.polygons:
            nw = (normal_matrix @ poly.normal).normalized()
            cw = world_matrix @ poly.center
            vd = (cw - cam_loc).normalized()
            poly_normal_world.append(nw)
            poly_is_front.append(nw.dot(vd) <= 0.0)

        edge_to_polys = defaultdict(list)
        for p in mesh.polygons:
            for li in range(p.loop_start, p.loop_start + p.loop_total):
                ei = mesh.loops[li].edge_index
                edge_to_polys[ei].append(p.index)

        poly_visible = [False] * len(mesh.polygons)
        poly_depth = [0.0] * len(mesh.polygons)
        # Dedup coincident faces (e.g. from solidify modifiers): two polygons that
        # project to the same 2D outline render to the same pixels, so keep only the
        # one closest to the camera.
        seen_screen_keys = {}
        for poly in mesh.polygons:
            if not poly_is_front[poly.index]:
                continue
            if any(screen_verts[i][2] <= 0.0 for i in poly.vertices):
                continue
            pd = sum(screen_verts[i][2] for i in poly.vertices) / poly.loop_total
            key = frozenset(
                (round(screen_verts[i][0], 1), round(screen_verts[i][1], 1))
                for i in poly.vertices
            )
            existing = seen_screen_keys.get(key)
            if existing is not None:
                old_depth, old_idx = existing
                if pd < old_depth:
                    poly_visible[old_idx] = False
                    poly_depth[old_idx] = 0.0
                else:
                    continue
            seen_screen_keys[key] = (pd, poly.index)
            poly_visible[poly.index] = True
            poly_depth[poly.index] = pd

        edge_kept = {}
        for poly in mesh.polygons:
            if not poly_visible[poly.index]:
                continue
            n1 = poly_normal_world[poly.index]
            for li in range(poly.loop_start, poly.loop_start + poly.loop_total):
                edge_index = mesh.loops[li].edge_index
                if (poly.index, edge_index) in edge_kept:
                    continue
                neighbors = [n for n in edge_to_polys[edge_index] if n != poly.index]
                draw = False
                if not neighbors:
                    draw = True
                elif any(not poly_is_front[n] for n in neighbors):
                    draw = True
                else:
                    for nb in neighbors:
                        d = max(-1.0, min(1.0, n1.dot(poly_normal_world[nb])))
                        if math.acos(d) >= crease_threshold:
                            draw = True
                            break
                edge_kept[(poly.index, edge_index)] = draw

        polys_out = []
        paths_out = []
        edges_out = []

        if shading_mode == "flat":
            parent = list(range(len(mesh.polygons)))

            def find(x):
                r = x
                while parent[r] != r:
                    r = parent[r]
                while parent[x] != r:
                    parent[x], x = r, parent[x]
                return r

            for poly in mesh.polygons:
                if not poly_visible[poly.index]:
                    continue
                mat_idx = poly.material_index
                for li in range(poly.loop_start, poly.loop_start + poly.loop_total):
                    edge_index = mesh.loops[li].edge_index
                    if edge_kept[(poly.index, edge_index)]:
                        continue
                    for other in edge_to_polys[edge_index]:
                        if other == poly.index or not poly_visible[other]:
                            continue
                        if mesh.polygons[other].material_index != mat_idx:
                            continue
                        ra, rb = find(poly.index), find(other)
                        if ra != rb:
                            parent[ra] = rb

            components = defaultdict(list)
            for pi in range(len(mesh.polygons)):
                if poly_visible[pi]:
                    components[find(pi)].append(pi)

            for poly_indices in components.values():
                first = mesh.polygons[poly_indices[0]]
                base = material_base_color(eval_obj, first)
                fill = rgb_to_hex(base)
                depth = sum(poly_depth[pi] for pi in poly_indices) / len(poly_indices)

                if len(poly_indices) == 1:
                    pi = poly_indices[0]
                    poly = mesh.polygons[pi]
                    n_loops = poly.loop_total
                    points = [(screen_verts[v][0], screen_verts[v][1]) for v in poly.vertices]
                    kept = []
                    for k in range(n_loops):
                        li = poly.loop_start + k
                        ei = mesh.loops[li].edge_index
                        if edge_kept[(pi, ei)]:
                            kept.append((points[k], points[(k + 1) % n_loops]))
                    all_kept = len(kept) == n_loops
                    polys_out.append((depth, points, fill, all_kept))
                    if not all_kept:
                        edges_out.extend(kept)
                    continue

                segments = []
                seen = set()
                for pi in poly_indices:
                    poly = mesh.polygons[pi]
                    n_loops = poly.loop_total
                    for k in range(n_loops):
                        li = poly.loop_start + k
                        ei = mesh.loops[li].edge_index
                        if not edge_kept[(pi, ei)]:
                            continue
                        va = poly.vertices[k]
                        vb = poly.vertices[(k + 1) % n_loops]
                        key = (min(va, vb), max(va, vb))
                        if key in seen:
                            continue
                        seen.add(key)
                        pa = (screen_verts[va][0], screen_verts[va][1])
                        pb = (screen_verts[vb][0], screen_verts[vb][1])
                        segments.append((va, vb, pa, pb))

                loops, is_clean = chain_segments(segments)
                if is_clean and loops:
                    d_parts = []
                    for loop in loops:
                        if not loop:
                            continue
                        d_parts.append(f"M{loop[0][0]:.2f},{loop[0][1]:.2f}")
                        for pt in loop[1:]:
                            d_parts.append(f"L{pt[0]:.2f},{pt[1]:.2f}")
                        d_parts.append("Z")
                    paths_out.append((depth, " ".join(d_parts), fill))
                    continue

                # Fallback: chaining failed (open chains from orphaned edges, T-junctions,
                # etc.). Emit each polygon individually with same-fill seam-mask stroke,
                # plus the component's kept edges as separate lines.
                for pi in poly_indices:
                    poly = mesh.polygons[pi]
                    pts = [(screen_verts[v][0], screen_verts[v][1]) for v in poly.vertices]
                    polys_out.append((poly_depth[pi], pts, fill, False))
                for sa, sb, pa, pb in segments:
                    edges_out.append((pa, pb))
        else:
            for poly in mesh.polygons:
                if not poly_visible[poly.index]:
                    continue
                n_loops = poly.loop_total
                points = [(screen_verts[v][0], screen_verts[v][1]) for v in poly.vertices]
                kept = []
                for k in range(n_loops):
                    li = poly.loop_start + k
                    ei = mesh.loops[li].edge_index
                    if edge_kept[(poly.index, ei)]:
                        kept.append((points[k], points[(k + 1) % n_loops]))
                n1 = poly_normal_world[poly.index]
                base = material_base_color(eval_obj, poly)
                color = shade_lambert(n1, base, lights)
                fill = rgb_to_hex(color)
                all_kept = len(kept) == n_loops
                polys_out.append((poly_depth[poly.index], points, fill, all_kept))
                if not all_kept:
                    edges_out.extend(kept)

        eval_obj.to_mesh_clear()

        if polys_out or paths_out:
            depths = [p[0] for p in polys_out] + [p[0] for p in paths_out]
            mesh_depth = sum(depths) / len(depths)
            mesh_groups.append((mesh_depth, polys_out, paths_out, edges_out))

    mesh_groups.sort(key=lambda g: -g[0])

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]
    sw = f"{stroke_width:g}"
    for _, polys_out, paths_out, edges_out in mesh_groups:
        shapes = (
            [("poly", d, pts, fill, ak) for d, pts, fill, ak in polys_out]
            + [("path", d, dpath, fill, None) for d, dpath, fill in paths_out]
        )
        for kind, _, payload, fill, all_edges_kept in sorted(shapes, key=lambda s: -s[1]):
            if kind == "poly":
                pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in payload)
                if all_edges_kept:
                    parts.append(
                        f'<polygon points="{pts}" fill="{fill}" stroke="#000000" '
                        f'stroke-width="{sw}" stroke-linejoin="round"/>'
                    )
                else:
                    parts.append(
                        f'<polygon points="{pts}" fill="{fill}" stroke="{fill}" '
                        f'stroke-width="0.6" stroke-linejoin="round"/>'
                    )
            else:
                parts.append(
                    f'<path d="{payload}" fill="{fill}" fill-rule="evenodd" '
                    f'stroke="#000000" stroke-width="{sw}" '
                    f'stroke-linejoin="round" stroke-linecap="round"/>'
                )
        for (x1, y1), (x2, y2) in edges_out:
            parts.append(
                f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                f'stroke="#000000" stroke-width="{sw}" stroke-linecap="round"/>'
            )
    parts.append('</svg>')

    if output_path is None:
        blend_path = bpy.data.filepath
        if not blend_path:
            print("ERROR: cannot infer output path; pass one after --", file=sys.stderr)
            sys.exit(1)
        output_path = os.path.splitext(blend_path)[0] + ".svg"

    with open(output_path, "w") as fh:
        fh.write("\n".join(parts))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
