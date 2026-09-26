"""Blender headless: CLO .glb -> web-optimised model.glb (Draco) + poster.webp (+ model.usdz with usdz=1)

usage: blender -b --python glb_to_web.py -- <in.glb> <out_dir> [cloth_ratio] [skin_ratio] [scale] [key=value ...]

Every CLO export is different (mannequins from 95k to 2.4M faces, hundreds of topstitch meshes, hair as alpha
cards or as solid geometry), so the geometry is reduced against a global triangle budget:
  budget=250000    total triangles for the whole garment (default)
  body_max=100000  mannequin body (skin, solid hair, shoes) after decimation
  trims_max=40000  all "BindedTrim" topstitch meshes together (each capped at trim_each=1500)
  hair=A,B         force these material names to be treated as alpha hair cards (else auto-detected)
  usdz=1           also build model.usdz
Hair cards (alpha-textured materials in the head region) are kept intact and exported as a matte,
double-sided alpha cutout with hard-alpha textures; tiny materials (eyes, lashes) are kept intact.
"""
import bpy, sys, os, re, json, struct, shutil, subprocess
import numpy as np
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
positional = [a for a in argv if "=" not in a]
overrides = dict(a.split("=", 1) for a in argv if "=" in a)
src, out_dir = os.path.abspath(positional[0]), os.path.abspath(positional[1])   # absolute: Blender resolves texture paths against its own cwd
cloth_ratio = float(positional[2]) if len(positional) > 2 else 0.35
body_ratio = float(positional[3]) if len(positional) > 3 else 0.5
model_scale = float(positional[4]) if len(positional) > 4 else 1.0   # uniform scale baked into the geometry (0.9 tuned in Unity)
HAIR_OVERRIDE = {s.strip() for s in overrides.get("hair", "").split(",") if s.strip()}
BUDGET = int(overrides.get("budget", 250000))
BODY_MAX = int(overrides.get("body_max", 100000))
TRIMS_MAX = int(overrides.get("trims_max", 40000))
TRIM_EACH = int(overrides.get("trim_each", 1500))
TINY_FACES = 4000          # avatar materials below this (eyes, lashes, straps) are never decimated
JUNK_TARGET = 2000         # flat, absurdly dense avatar parts (shoe soles, ground slabs) collapse to this
MAKE_USDZ = overrides.get("usdz", "0") == "1"
HAIR_CUTOFF = 0.3          # alpha-clip threshold the user settled on in Unity; hair is matte, double-sided cutout
os.makedirs(out_dir, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
meshes = [o for o in bpy.data.objects if o.type == 'MESH']
if not meshes:
    sys.exit("no meshes in " + src)

# ---- flatten hierarchy and bake every object transform into its vertices (local == world from here on;
# the coverage test and the height-based classification rely on that)
from mathutils import Matrix
for obj in meshes:
    mw = obj.matrix_world.copy(); obj.parent = None
    obj.data.transform(mw)
    obj.matrix_world = Matrix.Identity(4)
    obj.data.update()
for e in [o for o in bpy.data.objects if o.type == 'EMPTY']:
    bpy.data.objects.remove(e, do_unlink=True)

# the mannequin is the mesh CLO names "avatar"; "BindedTrim_*" are topstitch meshes; the rest is cloth
avatar = next((o for o in meshes if 'avatar' in o.name.lower()), None)
if avatar is not None:
    avatar.name = 'Avatar'; avatar.data.name = 'Avatar'
trim_meshes = [o for o in meshes if o is not avatar and o.name.startswith('BindedTrim')]
cloth_meshes = [o for o in meshes if o is not avatar and o not in trim_meshes]
if len(cloth_meshes) == 1:
    cloth_meshes[0].name = 'Cloth'; cloth_meshes[0].data.name = 'Cloth'
print("avatar:", avatar.name if avatar else None, "| cloth meshes:", len(cloth_meshes), "| trim meshes:", len(trim_meshes))
if avatar is None:
    print("WARNING: no mannequin mesh in this export (garment only)")

# ---- uniform scale baked into the vertices
if abs(model_scale - 1.0) > 1e-6:
    for obj in meshes:
        me = obj.data
        co = np.empty(len(me.vertices) * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        me.vertices.foreach_set("co", co * model_scale)
        me.update()
    print(f"applied model scale x{model_scale}")

# ---- bake KHR_texture_transform (Mapping nodes) into UVs
transforms, mapping_nodes = {}, {}
for mat in bpy.data.materials:
    if not mat.node_tree:
        continue
    for node in mat.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.inputs['Vector'].is_linked:
            srcn = node.inputs['Vector'].links[0].from_node
            if srcn.type == 'MAPPING':
                loc = srcn.inputs['Location'].default_value
                rot = srcn.inputs['Rotation'].default_value
                scl = srcn.inputs['Scale'].default_value
                transforms[mat.name] = (loc[0], loc[1], rot[2], scl[0], scl[1])
                mapping_nodes.setdefault(mat.name, set()).add(srcn.name)


def get_uvs(uvl, n):
    arr = np.empty(n * 2, dtype=np.float32)
    try:
        uvl.uv.foreach_get("vector", arr)
    except Exception:
        uvl.data.foreach_get("uv", arr)
    return arr.reshape(-1, 2)


def set_uvs(uvl, arr):
    flat = np.ascontiguousarray(arr, dtype=np.float32).ravel()
    try:
        uvl.uv.foreach_set("vector", flat)
    except Exception:
        uvl.data.foreach_set("uv", flat)


for obj in meshes:
    me = obj.data
    if not me.uv_layers:
        continue
    uvl = me.uv_layers[0]
    uvs = get_uvs(uvl, len(me.loops))
    pmat = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("material_index", pmat)
    ltot = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("loop_total", ltot)
    loop_mat = np.repeat(pmat, ltot)
    for si, mat in enumerate(me.materials):
        if mat is None or mat.name not in transforms:
            continue
        lx, ly, rz, sx, sy = transforms[mat.name]
        mask = loop_mat == si
        sub = uvs[mask] * np.array([sx, sy], dtype=np.float32)
        if abs(rz) > 1e-6:
            c, s = np.cos(rz), np.sin(rz)
            sub = np.stack([c * sub[:, 0] - s * sub[:, 1], s * sub[:, 0] + c * sub[:, 1]], axis=1)
        uvs[mask] = sub + np.array([lx, ly], dtype=np.float32)
    set_uvs(uvl, uvs)
for mname, names in mapping_nodes.items():
    nt = bpy.data.materials[mname].node_tree
    for n in names:
        nt.nodes.remove(nt.nodes[n])


# ---- merge duplicate materials
def base_name(n):
    return re.sub(r'\.\d{3}$', '', n)


for obj in meshes:
    me = obj.data
    slots = list(me.materials); first = {}; remap = {}
    for si, m in enumerate(slots):
        bn = base_name(m.name) if m else None
        if bn in first:
            remap[si] = first[bn]
        else:
            first[bn] = si; remap[si] = si
    uniq = sorted(set(remap.values()))
    lut = np.array([uniq.index(remap[i]) for i in range(len(slots))], dtype=np.int32)
    pmat = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("material_index", pmat)
    me.materials.clear()
    for ui in uniq:
        me.materials.append(slots[ui])
    me.polygons.foreach_set("material_index", lut[pmat])
used = {m for o in meshes for m in o.data.materials if m}
for m in list(bpy.data.materials):
    if m not in used:
        bpy.data.materials.remove(m)
for m in bpy.data.materials:
    m.name = base_name(m.name)


# ---- material helpers
def principled(mat):
    return next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None) if mat and mat.node_tree else None


def base_color_image(mat):
    """The image texture feeding Base Color, looking through mix/multiply nodes."""
    bsdf = principled(mat)
    if bsdf is None or not bsdf.inputs['Base Color'].is_linked:
        return None
    stack = [bsdf.inputs['Base Color'].links[0].from_node]; seen = set()
    while stack:
        n = stack.pop()
        if n.name in seen:
            continue
        seen.add(n.name)
        if n.type == 'TEX_IMAGE':
            return n.image
        for i in n.inputs:
            for l in i.links:
                stack.append(l.from_node)
    return None


_alpha_cache = {}


def image_has_alpha(img):
    if img is None:
        return False
    if img.name not in _alpha_cache:
        w, h = img.size
        if w == 0 or h == 0:
            _alpha_cache[img.name] = False
        else:
            px = np.empty(w * h * 4, dtype=np.float32)
            img.pixels.foreach_get(px)
            _alpha_cache[img.name] = bool(px[3::4].min() < 0.98)
    return _alpha_cache[img.name]


def is_blended(mat):
    return getattr(mat, 'surface_render_method', '') == 'BLENDED' or getattr(mat, 'blend_method', '') == 'BLEND'


def make_opaque(mat):
    """Solid geometry exported by CLO as 'blend' (hair without alpha, etc.): export it as plain opaque."""
    bsdf = principled(mat)
    if bsdf is not None:
        a = bsdf.inputs['Alpha']
        for l in list(a.links):
            mat.node_tree.links.remove(l)
        a.default_value = 1.0
    mat.surface_render_method = 'DITHERED'
    if hasattr(mat, 'blend_method'):
        mat.blend_method = 'OPAQUE'


def slot_stats(obj):
    me = obj.data
    co = np.empty(len(me.vertices) * 3, dtype=np.float32); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    pmat = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("material_index", pmat)
    ltot = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("loop_total", ltot)
    lverts = np.empty(len(me.loops), dtype=np.int32); me.loops.foreach_get("vertex_index", lverts)
    loop_mat = np.repeat(pmat, ltot)
    stats = {}
    for si, mat in enumerate(me.materials):
        if mat is None:
            continue
        vs = lverts[loop_mat == si]
        if len(vs) == 0:
            continue
        z = co[vs, 2]
        stats[mat.name] = dict(slot=si, faces=int((pmat == si).sum()), zmean=float(z.mean()), zmin=float(z.min()), zmax=float(z.max()),
                               centroid=co[vs].mean(axis=0), verts=np.unique(vs))
    return stats


# ---- classify the mannequin's materials: hair cards / tiny / junk / body
print("=== MATERIAL CLASSES (avatar) ===")
st = slot_stats(avatar) if avatar is not None else {}
zlo = min((s['zmin'] for s in st.values()), default=0.0); zhi = max((s['zmax'] for s in st.values()), default=1.0); H = max(zhi - zlo, 1e-6)
HAIR, OPAQUE_FORCE, classes, eyes_candidates = set(), set(), {}, []
for mat in (avatar.data.materials if avatar is not None else []):
    s = st.get(mat.name) if mat else None
    if not s:
        continue
    img = base_color_image(mat)
    blended = is_blended(mat)
    alpha_tex = image_has_alpha(img)
    head = (s['zmean'] - zlo) > 0.72 * H
    flat = (s['zmax'] - s['zmin']) < 0.02 * H
    if mat.name in HAIR_OVERRIDE or (alpha_tex and head and s['faces'] >= 200):
        cls = 'hair'; HAIR.add(mat.name)
    elif s['faces'] < TINY_FACES:
        cls = 'tiny'
        if not blended and img is not None and head:
            eyes_candidates.append(mat.name)
    elif flat and s['faces'] > 50000:
        cls = 'junk'
    else:
        cls = 'body'
    if cls != 'hair' and blended and not alpha_tex:
        make_opaque(mat); OPAQUE_FORCE.add(mat.name)
    classes[mat.name] = cls
    print(f"MAT {cls:5s} {mat.name}: faces={s['faces']} z={s['zmin']:.2f}..{s['zmax']:.2f} blended={blended} alphaTex={alpha_tex}{' -> made opaque' if mat.name in OPAQUE_FORCE else ''}")
print("HAIR:", sorted(HAIR), "(override)" if HAIR_OVERRIDE else "(detected)")
if avatar is not None and not HAIR:
    print("NOTE: no alpha hair cards on this mannequin (hair is solid geometry or absent)")

# ---- material fixes: bypass "black factor x texture" mixes, hair flags
print("=== MATERIAL FIXES ===")
for mat in bpy.data.materials:
    nt = mat.node_tree
    bsdf = principled(mat)
    if bsdf is None:
        continue
    inp = bsdf.inputs['Base Color']
    # CLO tints a neutral weave texture with the material's base-colour factor (glTF multiplies them); Blender
    # imports that as a multiply node and the exporter round-trips it, so it must stay. Only the hair cards
    # bypass it: their black factor would turn the brown strand textures black (the user chose the brown look).
    if mat.name in HAIR and inp.is_linked and inp.links[0].from_node.type != 'TEX_IMAGE':
        srcn = inp.links[0].from_node
        tex_img = base_color_image(mat)
        tex = next((n for n in nt.nodes if n.type == 'TEX_IMAGE' and n.image == tex_img), None) if tex_img else None
        if tex is not None:
            nt.links.new(tex.outputs['Color'], inp)
            print(f"{mat.name}: hair card, texture relinked directly (bypassed {srcn.type} tint)")
    if mat.name in HAIR:
        mat.surface_render_method = 'DITHERED'   # cutout, not blended
        mat.use_backface_culling = False         # both faces
        if hasattr(mat, 'blend_method'):         # legacy props the USD exporter still reads
            mat.blend_method = 'CLIP'
            mat.alpha_threshold = HAIR_CUTOFF
        bsdf.inputs['Roughness'].default_value = 1.0
        bsdf.inputs['Metallic'].default_value = 0.0
        for spec_name in ('Specular IOR Level', 'Specular'):
            if spec_name in bsdf.inputs:
                bsdf.inputs[spec_name].default_value = 0.0
                break


# ---- hide the mannequin under the garments
# CLO exports often leave skin poking through the fabric (unresolved simulation at the back of tight skirts), and
# once both surfaces are decimated they cross wherever the fabric lies a few millimetres off the skin. Three
# passes over the mannequin below the head (parts are the avatar's materials; head, hair and eyes are never touched):
#  1. push: along each skin vertex's normal the fabric layers are probed from 1 cm outside down to 8 cm inside
#     the skin; when no layer sits at least 6 mm outside the skin (the skin is at the surface of the garment or
#     pokes through it) the vertex is moved inward until it sits 6 mm under the innermost layer. Only on parts whose winding is
#     consistent, so the normal direction is reliable (signed volume fixes inverted parts); a penetrating layer
#     must lie in the near half of the part's thickness, which keeps thin parts (fingers) safe and stops front
#     skin from chasing a panel that penetrates the back.
#  2. visibility: a skin vertex is hidden when rays towards ~40 directions around the model (viewers from the
#     sides, up to 45 degrees above and slightly below) are all blocked by opaque cloth or by the mannequin itself. Both sides of
#     the surface are tested, so inconsistently wound parts (boots) cannot open holes.
#  3. fully hidden regions are deleted, keeping a one-ring rim.
VIS_DIRS, VIS_MIN_Z, VIS_MAX_Z, PROBE, NEAR_IN, CLEAR = 64, -0.35, 0.7, 0.01, 0.08, 0.006
if avatar is not None and cloth_meshes:
    from mathutils.bvhtree import BVHTree
    import bmesh
    me = avatar.data
    nv = len(me.vertices)
    co = np.empty(nv * 3, dtype=np.float32); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    nrm = np.empty(nv * 3, dtype=np.float32); me.vertex_normals.foreach_get("vector", nrm); nrm = nrm.reshape(-1, 3)
    pmat = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("material_index", pmat)
    ltot = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("loop_total", ltot)
    lstart = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("loop_start", lstart)
    lverts = np.empty(len(me.loops), dtype=np.int32); me.loops.foreach_get("vertex_index", lverts)
    loop_mat = np.repeat(pmat, ltot)
    lower = [n for n in st if classes.get(n) == 'body' and (st[n]['zmean'] - zlo) <= 0.72 * H]
    lower_slots = np.array([st[n]['slot'] for n in lower], dtype=np.int32)

    # winding per part: share of shared edges that the two faces run in opposite directions (1 = consistent);
    # the signed volume about the part's centroid then tells outward from inward
    nxt = np.arange(len(me.loops)) + 1
    nxt[lstart + ltot - 1] = lstart
    ea, eb = lverts.astype(np.int64), lverts[nxt].astype(np.int64)
    fwd, rev = ea * nv + eb, eb * nv + ea
    uniq, counts = np.unique(fwd, return_counts=True)
    dup = np.isin(fwd, uniq[counts > 1])
    opp = np.isin(fwd, rev)
    v0, v1, v2 = lverts[lstart], lverts[np.minimum(lstart + 1, len(lverts) - 1)], lverts[np.minimum(lstart + 2, len(lverts) - 1)]
    winding = {}
    for n in lower:
        sl = st[n]['slot']; fm = pmat == sl; lm = loop_mat == sl
        cen = st[n]['centroid']
        a, b, c = co[v0[fm]] - cen, co[v1[fm]] - cen, co[v2[fm]] - cen
        vol = float(np.einsum('ij,ij->i', a, np.cross(b, c)).sum() / 6.0)
        cons = int((opp & ~dup & lm).sum()); inc = int((dup & lm).sum())
        ratio = cons / max(cons + inc, 1)
        winding[n] = (ratio, vol)
        if ratio >= 0.9 and vol < 0:
            nrm[st[n]['verts']] *= -1.0
            print(f"COVER {n}: normals inverted, flipped")

    # occluders: opaque cloth (alpha-blended materials such as hair cards and zipper teeth do not hide anything)
    # plus the lower mannequin parts themselves (legs inside boots)
    def mesh_arrays(o):
        m = o.data
        c = np.empty(len(m.vertices) * 3, dtype=np.float32); m.vertices.foreach_get("co", c); c = c.reshape(-1, 3)
        pm = np.empty(len(m.polygons), dtype=np.int32); m.polygons.foreach_get("material_index", pm)
        lt = np.empty(len(m.polygons), dtype=np.int32); m.polygons.foreach_get("loop_total", lt)
        ls = np.empty(len(m.polygons), dtype=np.int32); m.polygons.foreach_get("loop_start", ls)
        lv = np.empty(len(m.loops), dtype=np.int32); m.loops.foreach_get("vertex_index", lv)
        return c, pm, lt, ls, lv
    cloth_verts, cloth_polys, base = [], [], 0
    for o in cloth_meshes:
        if not len(o.data.polygons):
            continue
        c, pm, lt, ls, lv = mesh_arrays(o)
        opaque = np.array([m is None or not (is_blended(m) and image_has_alpha(base_color_image(m))) for m in o.data.materials] or [True], dtype=bool)
        keep = opaque[np.clip(pm, 0, len(opaque) - 1)]
        cloth_polys += [tuple(int(x) + base for x in lv[s:s + t]) for s, t, k in zip(ls.tolist(), lt.tolist(), keep.tolist()) if k]
        cloth_verts += [tuple(v) for v in c.tolist()]; base += len(c)
    body_keep = np.isin(pmat, lower_slots)
    body_polys = [tuple(int(x) for x in lverts[s:s + t]) for s, t, k in zip(lstart.tolist(), ltot.tolist(), body_keep.tolist()) if k]
    cloth_bvh = BVHTree.FromPolygons(cloth_verts, cloth_polys, all_triangles=False) if cloth_polys else None
    body_bvh = BVHTree.FromPolygons([tuple(v) for v in co.tolist()], body_polys, all_triangles=False)
    test_verts = np.unique(np.concatenate([st[n]['verts'] for n in lower])) if lower else np.array([], dtype=np.int32)

    # 1. push skin back under the fabric
    slot_ok = {st[n]['slot'] for n in lower if winding[n][0] >= 0.9}
    vslot = np.full(nv, -1, dtype=np.int32); vslot[lverts] = loop_mat
    pushed_in = pushed_clear = 0; max_push = 0.0
    if cloth_bvh is not None:
        for vi in test_verts.tolist():
            if vslot[vi] not in slot_ok:
                continue
            p = Vector(co[vi]); n = Vector(nrm[vi])
            if n.length < 1e-6:
                continue
            # fabric layers along the normal, from PROBE outside the skin down to NEAR_IN inside it (f > 0: outside)
            f_min = f_max = None; travelled = 0.0
            while travelled < PROBE + NEAR_IN:
                hit = cloth_bvh.ray_cast(p + n * (PROBE - travelled), -n, PROBE + NEAR_IN - travelled)
                if hit[0] is None:
                    break
                travelled += hit[3] + 0.0002
                f = PROBE - travelled
                f_min = f if f_min is None else min(f_min, f)
                f_max = f if f_max is None else max(f_max, f)
            if f_min is None or f_max >= CLEAR:   # nothing there, or the outermost layer already covers this skin
                continue
            if f_min < 0:   # the skin pokes through: only if that fabric is in the near half of the part's thickness
                wall = body_bvh.ray_cast(p - n * 0.001, -n, 1.0)
                if -f_min >= (0.5 * wall[3] if wall[0] is not None else 0.02):
                    continue
                pushed_in += 1
            else:
                pushed_clear += 1
            move = CLEAR - f_min
            co[vi] -= nrm[vi] * move; max_push = max(max_push, move)
        me.vertices.foreach_set("co", co.ravel()); me.update()
        body_bvh = BVHTree.FromPolygons([tuple(v) for v in co.tolist()], body_polys, all_triangles=False)
    occ_verts = cloth_verts + [tuple(v) for v in co.tolist()]
    occ_polys = cloth_polys + [tuple(x + base for x in poly) for poly in body_polys]
    occ_bvh = BVHTree.FromPolygons(occ_verts, occ_polys, all_triangles=False)

    # 2. visibility from ~40 directions: a Fibonacci sphere without the steep views from below (nobody looks up a
    #    hem) or from above (such rays slide up the gap between skin and fabric and escape through the neckline)
    kk = np.arange(VIS_DIRS) + 0.5
    phi = np.arccos(1 - 2 * kk / VIS_DIRS); theta = np.pi * (1 + 5 ** 0.5) * kk
    dirs = np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1).astype(np.float32)
    dirs = dirs[(dirs[:, 2] >= VIS_MIN_Z) & (dirs[:, 2] <= VIS_MAX_Z)]
    dvec = [Vector(d) for d in dirs.tolist()]
    dots = nrm[test_verts] @ dirs.T if len(test_verts) else np.zeros((0, len(dirs)))
    hidden = np.zeros(nv, dtype=bool)
    for row, vi in enumerate(test_verts.tolist()):
        p = Vector(co[vi]); n = Vector(nrm[vi])
        if n.length < 1e-6:
            continue
        vis = False
        for j in np.nonzero(np.abs(dots[row]) >= 0.15)[0].tolist():
            if occ_bvh.ray_cast(p + n * (0.002 if dots[row, j] > 0 else -0.002), dvec[j], 8.0)[0] is None:
                vis = True; break
        hidden[vi] = not vis

    # 3. delete what nobody can see, eroded by one vertex ring
    ev = np.empty(len(me.edges) * 2, dtype=np.int32); me.edges.foreach_get("vertices", ev); ev = ev.reshape(-1, 2)
    exposed_edge = ~hidden[ev[:, 0]] | ~hidden[ev[:, 1]]
    rim = np.zeros(nv, dtype=bool); rim[ev[exposed_edge].ravel()] = True
    deletable = hidden & ~rim
    bm = bmesh.new(); bm.from_mesh(me); bm.verts.ensure_lookup_table(); bm.faces.ensure_lookup_table()
    del_faces = [f for f in bm.faces if all(deletable[v.index] for v in f.verts)]
    n_faces_before = len(bm.faces)
    if del_faces:
        bmesh.ops.delete(bm, geom=del_faces, context='FACES')
    bm.to_mesh(me); bm.free(); me.update()
    print("COVER parts:", {n: f"hidden {int(hidden[st[n]['verts']].sum())}/{len(st[n]['verts'])} winding {winding[n][0]:.2f} vol {winding[n][1]:+.4f}" for n in lower})
    print(f"COVER lower-body verts={len(test_verts)} pushed back inside={pushed_in} clearance={pushed_clear} (max {max_push * 1000:.0f} mm) hidden={int(hidden.sum())} faces removed={n_faces_before - len(me.polygons)}")
    st = slot_stats(avatar)   # refresh stats: face counts changed

# ---- decimation against the global budget
def apply_mod(obj, mod):
    bpy.context.view_layer.objects.active = obj
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def decimate_whole(obj, ratio):
    before = len(obj.data.polygons)
    if ratio >= 0.999 or before < 200:
        return before, before
    mod = obj.modifiers.new("dec", 'DECIMATE'); mod.decimate_type = 'COLLAPSE'; mod.use_collapse_triangulate = True
    mod.ratio = max(ratio, 0.01)
    apply_mod(obj, mod)
    return before, len(obj.data.polygons)


def decimate_group(obj, vert_indices, group_faces, ratio, name):
    """Collapse only the vertex group so that ~(1 - ratio) of its faces disappear. Blender's ratio is
    relative to the whole mesh, so it is rescaled here; otherwise a small group gets wiped out."""
    total = len(obj.data.polygons)
    if ratio >= 0.999 or group_faces < 200:
        return total, total
    vg = obj.vertex_groups.new(name=name)
    vg.add([int(v) for v in vert_indices], 1.0, 'REPLACE')
    remove = group_faces * (1.0 - ratio)
    mod = obj.modifiers.new("dec_" + name, 'DECIMATE'); mod.decimate_type = 'COLLAPSE'; mod.use_collapse_triangulate = True
    mod.ratio = max(1.0 - remove / max(total, 1), 0.01)
    mod.vertex_group = name; mod.vertex_group_factor = 1.0
    apply_mod(obj, mod)
    return total, len(obj.data.polygons)


print("=== DECIMATE (budget %d) ===" % BUDGET)
kept = 0; body_faces = 0; junk = {}
if avatar is not None:
    for n, s in st.items():
        c = classes.get(n)
        if c in ('hair', 'tiny'):
            kept += s['faces']
        elif c == 'junk':
            junk[n] = s
        else:
            body_faces += s['faces']
body_target = min(body_faces, int(body_faces * body_ratio), BODY_MAX) if body_faces else 0
trim_faces = {o.name: len(o.data.polygons) for o in trim_meshes}
trim_targets = {n: min(f, int(f * cloth_ratio), TRIM_EACH) for n, f in trim_faces.items()}
if sum(trim_targets.values()) > TRIMS_MAX:
    k = TRIMS_MAX / sum(trim_targets.values())
    trim_targets = {n: max(int(t * k), 50) for n, t in trim_targets.items()}
cloth_faces = {o.name: len(o.data.polygons) for o in cloth_meshes}
cloth_total = sum(cloth_faces.values())
cloth_budget = max(BUDGET - kept - body_target - sum(trim_targets.values()) - JUNK_TARGET * len(junk), int(cloth_total * 0.05))
cloth_r = min(cloth_ratio, cloth_budget / max(cloth_total, 1))
print(f"PLAN kept(hair+tiny)={kept} body {body_faces}->{body_target} trims {sum(trim_faces.values())}->{sum(trim_targets.values())} junk {sum(s['faces'] for s in junk.values())}->{JUNK_TARGET * len(junk)} cloth {cloth_total}->{int(cloth_total * cloth_r)} (ratio {cloth_r:.3f})")

for obj in cloth_meshes:
    b, a = decimate_whole(obj, cloth_r)
    print(f"{obj.name}: {b} -> {a} tris")
for obj in trim_meshes:
    t = trim_targets[obj.name]
    b, a = decimate_whole(obj, t / max(trim_faces[obj.name], 1))
    if b != a:
        print(f"{obj.name}: {b} -> {a} tris")
if trim_meshes:
    print(f"trims total: {sum(trim_faces.values())} -> {sum(len(o.data.polygons) for o in trim_meshes)} tris")
if avatar is not None:
    # each pass renumbers vertices, so the next group's indices are recomputed from a fresh material scan
    for n in list(junk):
        cur = slot_stats(avatar).get(n)
        if not cur:
            continue
        b, a = decimate_group(avatar, cur['verts'], cur['faces'], JUNK_TARGET / cur['faces'], "junk_" + str(cur['slot']))
        avatar.data.validate(verbose=False)
        print(f"Avatar junk {n}: {b} -> {a} tris (flat slab of {cur['faces']} faces)")
    if body_faces:
        cur = slot_stats(avatar)
        body_now = [s for n, s in cur.items() if classes.get(n) == 'body']
        faces_now = sum(s['faces'] for s in body_now)
        verts = np.unique(np.concatenate([s['verts'] for s in body_now]))
        b, a = decimate_group(avatar, verts, faces_now, body_target / max(faces_now, 1), "body")
        avatar.data.validate(verbose=False)
        print(f"Avatar body: {b} -> {a} tris")
total_now = sum(len(o.data.polygons) for o in meshes)
print(f"TOTAL after decimation: {total_now} tris")

# ---- downscale textures and re-encode them to real files, so both exporters embed the small versions
# (the glTF exporter copies packed originals byte-for-byte and ignores in-memory scaling)
tex_dir = os.path.join(out_dir, "_tex")
os.makedirs(tex_dir, exist_ok=True)
# images used by the hair materials get a hard alpha (1 above the cutoff, 0 below): AR Quick Look and
# blended renderers otherwise show the semi-transparent strands as see-through
hair_images = set()
for mat in bpy.data.materials:
    if mat.name in HAIR:
        for n in mat.node_tree.nodes:
            if n.type == 'TEX_IMAGE' and n.image is not None:
                hair_images.add(n.image.name)
print("=== TEXTURES ===")
for img in list(bpy.data.images):
    if img.type != 'IMAGE':
        continue
    w, h = img.size  # touching size loads packed image data
    if w == 0 or h == 0:
        print(f"{img.name}: no pixel data, skipped")
        continue
    has_alpha = image_has_alpha(img)
    t = 2048 if max(w, h) >= 3000 else (1024 if max(w, h) > 1024 else None)
    if t:
        img.scale(int(w * t / max(w, h)), int(h * t / max(w, h)))
    nw, nh = img.size
    fmt = 'PNG' if (img.file_format == 'PNG' and has_alpha) else 'JPEG'   # opaque PNGs become JPEG: same look, far smaller
    ext = '.png' if fmt == 'PNG' else '.jpg'
    new = bpy.data.images.new(img.name + "_web", nw, nh, alpha=(fmt == 'PNG'))
    new.colorspace_settings.name = img.colorspace_settings.name
    new.alpha_mode = img.alpha_mode
    px = np.empty(nw * nh * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    hard_alpha = img.name in hair_images and fmt == 'PNG'
    if hard_alpha:
        px[3::4] = (px[3::4] >= HAIR_CUTOFF).astype(np.float32)
    new.pixels.foreach_set(px)
    new.file_format = fmt
    path = os.path.join(tex_dir, re.sub(r'[^A-Za-z0-9_.-]+', '_', img.name) + ext)
    new.filepath_raw = path
    try:
        new.save(filepath=path, quality=80)
    except TypeError:
        new.save()
    new.colorspace_settings.name = img.colorspace_settings.name
    for mat in bpy.data.materials:
        for n in mat.node_tree.nodes:
            if n.type == 'TEX_IMAGE' and n.image == img:
                n.image = new
    print(f"TEX {img.name}: {w}x{h} -> {nw}x{nh} {fmt} {os.path.getsize(path)} bytes{' HARD-ALPHA(hair)' if hard_alpha else ''}")
    bpy.data.images.remove(img)

# ---- face direction for the poster camera: from the head's skin to the face parts (eyes, lashes, brows: all of
# them averaged, since some exports split the eyes into a left and a right material) if we can find them, else
# CLO's default (-Y)
fwd = Vector((0.0, -1.0, 0.0))
head_bodies = [n for n, s in st.items() if classes.get(n) == 'body' and (s['zmean'] - zlo) > 0.72 * H]
if avatar is not None and head_bodies and eyes_candidates:
    head_c = st[max(head_bodies, key=lambda n: st[n]['faces'])]['centroid']
    eyes_c = np.mean([st[n]['centroid'] for n in eyes_candidates if n in st], axis=0)
    v = Vector((float(eyes_c[0] - head_c[0]), float(eyes_c[1] - head_c[1]), 0.0))
    if v.length > 0.01:
        fwd = v.normalized()
print("forward (blender xy):", tuple(round(v, 3) for v in fwd))

# ---- exports
glb_path = os.path.join(out_dir, "model.glb")
bpy.ops.export_scene.gltf(
    filepath=glb_path, export_format='GLB', export_apply=True,
    export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=6,
    export_draco_position_quantization=14, export_draco_normal_quantization=10, export_draco_texcoord_quantization=12,
    export_image_format='AUTO', export_jpeg_quality=80, export_image_quality=80,
    export_animations=False, export_skins=False, export_yup=True, export_materials='EXPORT',
    export_normals=True, export_tangents=False)
print("GLB", os.path.getsize(glb_path))

# ---- USD (optional, usdz=1): export a plain .usdc + textures, patch it with pxr (usd_fix.py), package as .usdz.
if MAKE_USDZ:
    usd_dir = os.path.join(out_dir, "_usd")
    shutil.rmtree(usd_dir, ignore_errors=True)
    os.makedirs(usd_dir, exist_ok=True)
    usdc_path = os.path.join(usd_dir, "model.usdc")
    bpy.ops.wm.usd_export(
        filepath=usdc_path, export_materials=True, export_textures_mode='NEW', generate_preview_surface=True,
        convert_orientation=True, export_global_forward_selection='NEGATIVE_Z', export_global_up_selection='Y',
        export_animation=False, export_armatures=False, triangulate_meshes=True, export_custom_properties=False)
    usdz_path = os.path.join(out_dir, "model.usdz")
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        sys.path.insert(0, tools_dir)
        import usd_fix
        for line in usd_fix.fix_and_package(usd_dir, usdz_path, HAIR_CUTOFF, HAIR):
            print("USD", line)
    except ImportError as e:
        print("pxr not usable inside Blender (", e, ") -> running usd_fix.py with system python")
        subprocess.run(["python", os.path.join(tools_dir, "usd_fix.py"), usd_dir, usdz_path, str(HAIR_CUTOFF), *sorted(HAIR)], check=True)
    shutil.rmtree(usd_dir, ignore_errors=True)
    print("USDZ", os.path.getsize(usdz_path))
else:
    print("USDZ skipped (pass usdz=1 to generate)")

# ---- poster render
scene = bpy.context.scene
engines = [i.identifier for i in scene.render.bl_rna.properties['engine'].enum_items]
scene.render.engine = next((e for e in engines if 'EEVEE' in e), engines[0])
scene.render.resolution_x = scene.render.resolution_y = 1024
scene.render.film_transparent = True
scene.render.image_settings.file_format = 'WEBP'
scene.render.image_settings.quality = 85
scene.render.image_settings.color_mode = 'RGBA'
try:
    scene.eevee.taa_render_samples = 32
except Exception:
    pass
world = bpy.data.worlds.new("W"); scene.world = world; world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
bg.inputs[0].default_value = (0.8, 0.8, 0.85, 1); bg.inputs[1].default_value = 1.0
cam_data = bpy.data.cameras.new("Cam"); cam = bpy.data.objects.new("Cam", cam_data)
scene.collection.objects.link(cam); scene.camera = cam
cam_data.lens = 60
target = Vector((0.0, 0.0, 0.95 * model_scale))
side = Vector((-fwd.y, fwd.x, 0.0))
cam.location = target + fwd * 2.9 + side * 1.0 + Vector((0.0, 0.0, 0.35))
cam.rotation_euler = (target - cam.location).to_track_quat('-Z', 'Y').to_euler()
sun_d = bpy.data.lights.new("Sun", 'SUN'); sun_d.energy = 3.0
sun = bpy.data.objects.new("Sun", sun_d); scene.collection.objects.link(sun)
sun.rotation_euler = (0.9, 0.3, fwd.to_track_quat('Y', 'Z').to_euler().z + 0.6)
poster = os.path.join(out_dir, "poster.webp")
scene.render.filepath = poster
bpy.ops.render.render(write_still=True)
print("POSTER", os.path.getsize(poster) if os.path.exists(poster) else "missing")
# optional QA renders (front and back, not published): qa_dir=<folder>
qa_dir = overrides.get("qa_dir")
if qa_dir:
    os.makedirs(qa_dir, exist_ok=True)
    scene.render.resolution_x = scene.render.resolution_y = 640
    for tag, direction in (("front", fwd), ("back", -fwd)):
        cam.location = target + direction * 2.9 + Vector((0.0, 0.0, 0.35))
        cam.rotation_euler = (target - cam.location).to_track_quat('-Z', 'Y').to_euler()
        scene.render.filepath = os.path.join(qa_dir, f"{tag}.webp")
        bpy.ops.render.render(write_still=True)
    print("QA renders:", qa_dir)
shutil.rmtree(tex_dir, ignore_errors=True)

# ---- post-pass on the GLB json: enforce hair and opaque flags, report
with open(glb_path, "rb") as f:
    magic, ver, length = struct.unpack("<III", f.read(12))
    clen, ctype = struct.unpack("<II", f.read(8))
    js = json.loads(f.read(clen))
    rest = f.read()
for m in js.get("materials", []):
    if m.get("name") in HAIR:
        m["alphaMode"] = "MASK"
        m["alphaCutoff"] = HAIR_CUTOFF
        m["doubleSided"] = True
        pbr = m.setdefault("pbrMetallicRoughness", {})
        pbr["baseColorFactor"] = [1, 1, 1, 1]
        pbr["metallicFactor"] = 0.0
        pbr["roughnessFactor"] = 1.0
        m.setdefault("extensions", {})["KHR_materials_specular"] = {"specularFactor": 0.0}
    elif m.get("name") in OPAQUE_FORCE:
        m["alphaMode"] = "OPAQUE"
        m.pop("alphaCutoff", None)
if "KHR_materials_specular" not in js.setdefault("extensionsUsed", []):
    js["extensionsUsed"].append("KHR_materials_specular")
jb = json.dumps(js, separators=(",", ":")).encode("utf-8")
jb += b" " * ((4 - len(jb) % 4) % 4)
with open(glb_path, "wb") as f:
    f.write(struct.pack("<III", magic, ver, 12 + 8 + len(jb) + len(rest)))
    f.write(struct.pack("<II", len(jb), ctype))
    f.write(jb)
    f.write(rest)
print("=== GLB SUMMARY ===")
print("extensionsRequired:", js.get("extensionsRequired"), "used:", js.get("extensionsUsed"))
tris = 0
for mesh in js["meshes"]:
    for pr in mesh["primitives"]:
        tris += js["accessors"][pr["indices"]]["count"] // 3
print(f"meshes: {len(js['meshes'])} total tris: {tris}")
for m in js["materials"]:
    pbr = m.get("pbrMetallicRoughness", {})
    print(f"  {m['name']}: alpha={m.get('alphaMode', 'OPAQUE')} cutoff={m.get('alphaCutoff')} ds={m.get('doubleSided', False)} bcTex={pbr.get('baseColorTexture', {}).get('index')} nm={m.get('normalTexture', {}).get('index')}")
img_bytes = 0
for i, im in enumerate(js.get("images", [])):
    bv = js["bufferViews"][im["bufferView"]]; img_bytes += bv['byteLength']
print(f"images: {len(js.get('images', []))} ({img_bytes / 1e6:.1f} MB)")
print("FINAL GLB", os.path.getsize(glb_path))
