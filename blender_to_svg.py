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
    lights = get_viewport_lights(camera)

    faces = []

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

        for poly in mesh.polygons:
            if not poly_is_front[poly.index]:
                continue
            verts_idx = poly.vertices
            if any(screen_verts[i][2] <= 0.0 for i in verts_idx):
                continue

            n_loops = poly.loop_total
            points = [(screen_verts[i][0], screen_verts[i][1]) for i in verts_idx]

            kept_edges = []
            n1 = poly_normal_world[poly.index]
            for k in range(n_loops):
                loop_index = poly.loop_start + k
                edge_index = mesh.loops[loop_index].edge_index
                neighbors = [n for n in edge_to_polys[edge_index] if n != poly.index]
                draw = False
                if not neighbors:
                    draw = True
                elif any(not poly_is_front[n] for n in neighbors):
                    draw = True
                else:
                    for nb in neighbors:
                        d = max(-1.0, min(1.0, n1.dot(poly_normal_world[nb])))
                        angle = math.acos(d)
                        if angle >= crease_threshold:
                            draw = True
                            break
                if draw:
                    kept_edges.append((points[k], points[(k + 1) % n_loops]))

            depth = sum(screen_verts[i][2] for i in verts_idx) / len(verts_idx)
            base = material_base_color(eval_obj, poly)
            shaded = shade_lambert(n1, base, lights)
            fill = rgb_to_hex(shaded)
            faces.append((depth, points, fill, kept_edges))

        eval_obj.to_mesh_clear()

    faces.sort(key=lambda f: -f[0])

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]
    sw = f"{stroke_width:g}"
    for _, points, fill, kept_edges in faces:
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        parts.append(
            f'<polygon points="{pts}" fill="{fill}" stroke="{fill}" '
            f'stroke-width="0.6"/>'
        )
        for (x1, y1), (x2, y2) in kept_edges:
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
