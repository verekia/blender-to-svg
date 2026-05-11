"""Unit tests for blender_to_svg.py.

Run with:  python3 test_blender_to_svg.py
(no Blender required — bpy/mathutils/bpy_extras are stubbed below).

Exits non-zero if any test fails. Covers the pure helpers; the actual
Blender-driven pipeline still needs an end-to-end run on the .blend
fixtures (see CLAUDE.md, "When testing changes").
"""

import math
import sys
import types
import traceback


# --- stub bpy & friends so blender_to_svg can import outside Blender ---

class _StubVector:
    def __init__(self, v=(0.0, 0.0, 0.0)):
        v = tuple(v)
        if len(v) == 2:
            v = (v[0], v[1], 0.0)
        self.x = float(v[0])
        self.y = float(v[1])
        self.z = float(v[2])

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __getitem__(self, i):
        return (self.x, self.y, self.z)[i]

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def normalized(self):
        L = self.length
        if L == 0.0:
            return _StubVector((0.0, 0.0, 0.0))
        return _StubVector((self.x / L, self.y / L, self.z / L))

    def dot(self, o):
        return self.x * o.x + self.y * o.y + self.z * o.z


class _StubMatrix:
    @staticmethod
    def Rotation(*a, **k):
        return None


_mathutils = types.ModuleType("mathutils")
_mathutils.Vector = _StubVector
_mathutils.Matrix = _StubMatrix
sys.modules["mathutils"] = _mathutils

_bpy = types.ModuleType("bpy")
_bpy.context = types.SimpleNamespace()
sys.modules["bpy"] = _bpy

_bpy_extras = types.ModuleType("bpy_extras")
_bpy_extras_obj = types.ModuleType("bpy_extras.object_utils")
_bpy_extras_obj.world_to_camera_view = lambda *a, **k: None
sys.modules["bpy_extras"] = _bpy_extras
sys.modules["bpy_extras.object_utils"] = _bpy_extras_obj

import blender_to_svg as bts  # noqa: E402


# --- tiny test runner ---

_REGISTRY = []


def test(name):
    def deco(fn):
        _REGISTRY.append((name, fn))
        return fn
    return deco


def assert_eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg} expected {b!r}, got {a!r}")


def assert_close(a, b, tol=1e-6, msg=""):
    if abs(a - b) > tol:
        raise AssertionError(f"{msg} expected {b!r} ± {tol}, got {a!r}")


def assert_in(item, container, msg=""):
    if item not in container:
        raise AssertionError(f"{msg} {item!r} not in {container!r}")


# --- linear_to_srgb_byte ---

@test("linear_to_srgb_byte clamps out-of-range inputs")
def _():
    assert_eq(bts.linear_to_srgb_byte(-1.0), 0)
    assert_eq(bts.linear_to_srgb_byte(0.0), 0)
    assert_eq(bts.linear_to_srgb_byte(1.0), 255)
    assert_eq(bts.linear_to_srgb_byte(2.0), 255)


@test("linear_to_srgb_byte uses linear slope below 0.0031308 cutoff")
def _():
    # at cutoff
    assert_eq(bts.linear_to_srgb_byte(0.0031308), 10)
    # well below cutoff
    assert_eq(bts.linear_to_srgb_byte(0.001), round(12.92 * 0.001 * 255))


@test("linear_to_srgb_byte: linear 0.5 → 188 (reference value)")
def _():
    assert_eq(bts.linear_to_srgb_byte(0.5), 188)


# --- rgb_to_hex ---

@test("rgb_to_hex extremes")
def _():
    assert_eq(bts.rgb_to_hex((0.0, 0.0, 0.0)), "#000000")
    assert_eq(bts.rgb_to_hex((1.0, 1.0, 1.0)), "#ffffff")


@test("rgb_to_hex primaries")
def _():
    assert_eq(bts.rgb_to_hex((1.0, 0.0, 0.0)), "#ff0000")
    assert_eq(bts.rgb_to_hex((0.0, 1.0, 0.0)), "#00ff00")
    assert_eq(bts.rgb_to_hex((0.0, 0.0, 1.0)), "#0000ff")


@test("rgb_to_hex clamps over-bright values")
def _():
    assert_eq(bts.rgb_to_hex((2.0, -0.5, 0.5)), "#ff00bc")


# --- shade_lambert ---

@test("shade_lambert: light directly on surface = ambient + full diffuse")
def _():
    n = _StubVector((0, 0, 1))
    d = _StubVector((0, 0, 1))
    col = _StubVector((1, 1, 1))
    r, g, b = bts.shade_lambert(n, (0.5, 0.5, 0.5), [(d, col)])
    # ambient(0.05) * 0.5 + 0.5 * 1 * 1 = 0.525
    assert_close(r, 0.525)
    assert_close(g, 0.525)
    assert_close(b, 0.525)


@test("shade_lambert: light behind surface contributes only ambient")
def _():
    n = _StubVector((0, 0, 1))
    d = _StubVector((0, 0, -1))
    col = _StubVector((1, 1, 1))
    r, g, b = bts.shade_lambert(n, (0.8, 0.8, 0.8), [(d, col)])
    assert_close(r, 0.05 * 0.8)
    assert_close(g, 0.05 * 0.8)
    assert_close(b, 0.05 * 0.8)


@test("shade_lambert: per-channel cap at 1.0")
def _():
    n = _StubVector((0, 0, 1))
    d = _StubVector((0, 0, 1))
    col = _StubVector((5, 5, 5))
    r, g, b = bts.shade_lambert(n, (1.0, 1.0, 1.0), [(d, col)])
    assert_eq(r, 1.0)
    assert_eq(g, 1.0)
    assert_eq(b, 1.0)


@test("shade_lambert: multiple lights sum per channel")
def _():
    n = _StubVector((0, 0, 1))
    d = _StubVector((0, 0, 1))
    c1 = _StubVector((0.3, 0.0, 0.0))
    c2 = _StubVector((0.0, 0.4, 0.0))
    r, g, b = bts.shade_lambert(
        n, (1.0, 1.0, 1.0), [(d, c1), (d, c2)], ambient=0.0
    )
    assert_close(r, 0.3)
    assert_close(g, 0.4)
    assert_close(b, 0.0)


@test("shade_lambert: oblique light scales with n·d")
def _():
    n = _StubVector((0, 0, 1))
    # 60° off normal → dot = 0.5
    d = _StubVector((math.sin(math.radians(60)), 0, math.cos(math.radians(60))))
    col = _StubVector((1, 1, 1))
    r, _, _ = bts.shade_lambert(n, (1.0, 1.0, 1.0), [(d, col)], ambient=0.0)
    assert_close(r, 0.5, tol=1e-9)


# --- chain_segments ---

@test("chain_segments: empty input is trivially clean")
def _():
    loops, clean = bts.chain_segments([])
    assert_eq(loops, [])
    assert_eq(clean, True)


@test("chain_segments: triangle closes into one 3-point loop")
def _():
    p0, p1, p2 = (0, 0), (1, 0), (0, 1)
    segs = [
        (0, 1, p0, p1),
        (1, 2, p1, p2),
        (2, 0, p2, p0),
    ]
    loops, clean = bts.chain_segments(segs)
    assert_eq(clean, True)
    assert_eq(len(loops), 1)
    assert_eq(len(loops[0]), 3)
    assert_eq(set(loops[0]), {p0, p1, p2})


@test("chain_segments: segment stored in reverse orientation still chains")
def _():
    # Third edge stored as (0, 2, ...) rather than (2, 0, ...) — exercises the
    # else branch in the traversal where sva != current_v.
    p0, p1, p2 = (0, 0), (1, 0), (0, 1)
    segs = [
        (0, 1, p0, p1),
        (1, 2, p1, p2),
        (0, 2, p0, p2),
    ]
    loops, clean = bts.chain_segments(segs)
    assert_eq(clean, True)
    assert_eq(len(loops), 1)


@test("chain_segments: open chain produces is_clean=False (megaxe regression)")
def _():
    # An orphaned-edge component: vertex 0 and vertex 2 have degree 1.
    # This is the case the fallback path was added for; chain_segments must
    # flag it so the caller can switch to per-polygon emission.
    p0, p1, p2 = (0, 0), (1, 0), (2, 0)
    segs = [
        (0, 1, p0, p1),
        (1, 2, p1, p2),
    ]
    _, clean = bts.chain_segments(segs)
    assert_eq(clean, False)


@test("chain_segments: two disjoint triangles → two loops, clean")
def _():
    a0, a1, a2 = (0, 0), (1, 0), (0, 1)
    b0, b1, b2 = (5, 5), (6, 5), (5, 6)
    segs = [
        (0, 1, a0, a1), (1, 2, a1, a2), (2, 0, a2, a0),
        (3, 4, b0, b1), (4, 5, b1, b2), (5, 3, b2, b0),
    ]
    loops, clean = bts.chain_segments(segs)
    assert_eq(clean, True)
    assert_eq(len(loops), 2)


@test("chain_segments: quad closes into one 4-point loop")
def _():
    p0, p1, p2, p3 = (0, 0), (1, 0), (1, 1), (0, 1)
    segs = [
        (0, 1, p0, p1),
        (1, 2, p1, p2),
        (2, 3, p2, p3),
        (3, 0, p3, p0),
    ]
    loops, clean = bts.chain_segments(segs)
    assert_eq(clean, True)
    assert_eq(len(loops), 1)
    assert_eq(len(loops[0]), 4)


# --- classify_flat_edges ---

@test("classify_flat_edges: empty input -> empty sets")
def _():
    interior, cancelled = bts.classify_flat_edges([])
    assert_eq(interior, set())
    assert_eq(cancelled, set())


@test("classify_flat_edges: singleton bucket is outline (no interior, no cancelled)")
def _():
    # one polygon owns this edge — boundary, not a crease
    kept = [(0, 5, 0, (10.0, 0.0), (20.0, 0.0))]
    interior, cancelled = bts.classify_flat_edges(kept)
    assert_eq(interior, set())
    assert_eq(cancelled, set())


@test("classify_flat_edges: two same-material polys sharing one mesh edge -> interior crease")
def _():
    # Regression for the dropped-crease bug. Two same-material polys, same mesh
    # edge index 5, coincident 2D segment. Direction reversed on poly 1
    # (manifold-neighbour winding) — must still bucket together.
    kept = [
        (0, 5, 0, (10.0, 0.0), (20.0, 0.0)),
        (1, 5, 0, (20.0, 0.0), (10.0, 0.0)),
    ]
    interior, cancelled = bts.classify_flat_edges(kept)
    assert_eq(interior, {(0, 5), (1, 5)})
    assert_eq(cancelled, set())


@test("classify_flat_edges: coincident edges from different mesh edges -> cancelled")
def _():
    # e.g. solidify inner+outer shell silhouettes projecting to the same screen edge
    kept = [
        (0, 5, 0, (10.0, 0.0), (20.0, 0.0)),
        (1, 7, 0, (10.0, 0.0), (20.0, 0.0)),
    ]
    interior, cancelled = bts.classify_flat_edges(kept)
    assert_eq(interior, set())
    assert_eq(cancelled, {(0, 5), (1, 7)})


@test("classify_flat_edges: same mesh edge across two materials -> both outlines")
def _():
    # Each material's bucket is a singleton -> outline on both sides, no overlay.
    kept = [
        (0, 5, 0, (10.0, 0.0), (20.0, 0.0)),
        (1, 5, 1, (20.0, 0.0), (10.0, 0.0)),
    ]
    interior, cancelled = bts.classify_flat_edges(kept)
    assert_eq(interior, set())
    assert_eq(cancelled, set())


@test("classify_flat_edges: 0.1 rounding tolerates near-coincident positions")
def _():
    # Solidify shells were ~0.03 user units apart on megaxe; round(_, 1) catches them.
    kept = [
        (0, 5, 0, (10.00, 0.00), (20.00, 0.00)),
        (1, 5, 0, (20.03, 0.02), (10.01, 0.01)),
    ]
    interior, _c = bts.classify_flat_edges(kept)
    assert_eq(interior, {(0, 5), (1, 5)})


@test("classify_flat_edges: zero-length edge (collapsed by rounding) is dropped")
def _():
    kept = [(0, 5, 0, (10.0, 0.0), (10.04, 0.04))]  # both ends round to same key
    interior, cancelled = bts.classify_flat_edges(kept)
    assert_eq(interior, set())
    assert_eq(cancelled, set())


@test("classify_flat_edges: three polys, only the shared diagonal is interior")
def _():
    # Two triangles sharing diagonal edge 5; a third unrelated triangle on edge 9.
    kept = [
        (0, 5, 0, (0.0, 0.0), (10.0, 10.0)),
        (1, 5, 0, (10.0, 10.0), (0.0, 0.0)),
        (2, 9, 0, (50.0, 50.0), (60.0, 60.0)),
    ]
    interior, cancelled = bts.classify_flat_edges(kept)
    assert_eq(interior, {(0, 5), (1, 5)})
    assert_eq(cancelled, set())


# --- parse_args ---

@test("parse_args: defaults when only `--` separator is present")
def _():
    orig = sys.argv
    try:
        sys.argv = ["blender", "--background", "--python", "x.py", "--"]
        args = bts.parse_args()
        assert_eq(args.output, None)
        assert_eq(args.stroke_width, 1.0)
        assert_eq(args.crease_angle, 0.0)
        assert_eq(args.shading, "lambert")
    finally:
        sys.argv = orig


@test("parse_args: all flags parsed after `--`")
def _():
    orig = sys.argv
    try:
        sys.argv = ["x", "--", "out.svg", "-w", "2.5", "-c", "45", "-s", "flat"]
        args = bts.parse_args()
        assert_eq(args.output, "out.svg")
        assert_eq(args.stroke_width, 2.5)
        assert_eq(args.crease_angle, 45.0)
        assert_eq(args.shading, "flat")
    finally:
        sys.argv = orig


@test("parse_args: no `--` separator → empty args, all defaults")
def _():
    orig = sys.argv
    try:
        sys.argv = ["x", "ignored.blend"]
        args = bts.parse_args()
        assert_eq(args.output, None)
        assert_eq(args.shading, "lambert")
    finally:
        sys.argv = orig


# --- material_base_color ---

@test("material_base_color: no material slots → DEFAULT_BASE")
def _():
    obj = types.SimpleNamespace(material_slots=[])
    poly = types.SimpleNamespace(material_index=0)
    assert_eq(bts.material_base_color(obj, poly), bts.DEFAULT_BASE)


@test("material_base_color: poly.material_index out of range falls back to slot 0")
def _():
    mat = types.SimpleNamespace(use_nodes=False, diffuse_color=(0.5, 0.5, 0.5, 1.0))
    slot = types.SimpleNamespace(material=mat)
    obj = types.SimpleNamespace(material_slots=[slot])
    poly = types.SimpleNamespace(material_index=99)
    assert_eq(bts.material_base_color(obj, poly), (0.5, 0.5, 0.5))


@test("material_base_color: slot with material=None → DEFAULT_BASE")
def _():
    slot = types.SimpleNamespace(material=None)
    obj = types.SimpleNamespace(material_slots=[slot])
    poly = types.SimpleNamespace(material_index=0)
    assert_eq(bts.material_base_color(obj, poly), bts.DEFAULT_BASE)


@test("material_base_color: Principled BSDF Base Color wins over diffuse_color")
def _():
    base_input = types.SimpleNamespace(default_value=(0.2, 0.4, 0.6, 1.0))
    node = types.SimpleNamespace(
        type="BSDF_PRINCIPLED",
        inputs={"Base Color": base_input},
    )
    tree = types.SimpleNamespace(nodes=[node])
    mat = types.SimpleNamespace(
        use_nodes=True, node_tree=tree, diffuse_color=(0.9, 0.9, 0.9, 1.0)
    )
    slot = types.SimpleNamespace(material=mat)
    obj = types.SimpleNamespace(material_slots=[slot])
    poly = types.SimpleNamespace(material_index=0)
    assert_eq(bts.material_base_color(obj, poly), (0.2, 0.4, 0.6))


@test("material_base_color: Emission node Color used when no Principled present")
def _():
    col_input = types.SimpleNamespace(default_value=(0.1, 0.7, 0.3, 1.0))
    node = types.SimpleNamespace(
        type="EMISSION",
        inputs={"Color": col_input},
    )
    tree = types.SimpleNamespace(nodes=[node])
    mat = types.SimpleNamespace(
        use_nodes=True, node_tree=tree, diffuse_color=(0.0, 0.0, 0.0, 1.0)
    )
    slot = types.SimpleNamespace(material=mat)
    obj = types.SimpleNamespace(material_slots=[slot])
    poly = types.SimpleNamespace(material_index=0)
    assert_eq(bts.material_base_color(obj, poly), (0.1, 0.7, 0.3))


@test("material_base_color: use_nodes=False falls back to mat.diffuse_color")
def _():
    mat = types.SimpleNamespace(use_nodes=False, diffuse_color=(0.25, 0.5, 0.75, 1.0))
    slot = types.SimpleNamespace(material=mat)
    obj = types.SimpleNamespace(material_slots=[slot])
    poly = types.SimpleNamespace(material_index=0)
    assert_eq(bts.material_base_color(obj, poly), (0.25, 0.5, 0.75))


# --- runner ---

def main():
    failed = []
    for name, fn in _REGISTRY:
        try:
            fn()
        except Exception as e:
            failed.append((name, e))
            print(f"FAIL  {name}")
            traceback.print_exc()
        else:
            print(f"PASS  {name}")
    total = len(_REGISTRY)
    print(f"\n{total - len(failed)}/{total} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
