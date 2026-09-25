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

# ---- flatten hierarchy
for obj in meshes:
    mw = obj.matrix_world.copy(); obj.parent = None; obj.matrix_world = mw
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
    if inp.is_linked and inp.links[0].from_node.type != 'TEX_IMAGE':
        srcn = inp.links[0].from_node
        tex_img = base_color_image(mat)
        tex = next((n for n in nt.nodes if n.type == 'TEX_IMAGE' and n.image == tex_img), None) if tex_img else None
        if tex is not None:
            nt.links.new(tex.outputs['Color'], inp)
            print(f"{mat.name}: relinked {tex.image.name} directly (bypassed {srcn.type})")
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

# ---- face direction for the poster camera: from the head's skin to the eyes if we can find them, else CLO's default (-Y)
fwd = Vector((0.0, -1.0, 0.0))
head_bodies = [n for n, s in st.items() if classes.get(n) == 'body' and (s['zmean'] - zlo) > 0.72 * H]
if avatar is not None and head_bodies and eyes_candidates:
    head_c = st[max(head_bodies, key=lambda n: st[n]['faces'])]['centroid']
    eyes_c = st[min(eyes_candidates, key=lambda n: st[n]['faces'])]['centroid']
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
