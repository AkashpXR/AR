"""Blender headless: CLO .glb -> web-optimised model.glb (Draco) + model.usdz + poster.webp

usage: blender -b --python glb_to_web.py -- <in.glb> <out_dir> [cloth_ratio] [skin_ratio]
"""
import bpy, sys, os, re, json, struct, shutil, subprocess
import numpy as np
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out_dir = os.path.abspath(argv[0]), os.path.abspath(argv[1])   # absolute: Blender resolves texture paths against its own cwd
cloth_ratio = float(argv[2]) if len(argv) > 2 else 0.35
skin_ratio = float(argv[3]) if len(argv) > 3 else 0.5
model_scale = float(argv[4]) if len(argv) > 4 else 1.0   # uniform scale baked into the geometry (0.9 tuned in Unity)
os.makedirs(out_dir, exist_ok=True)
HAIR = {"Material154152", "Material154157", "Material154162"}
HAIR_CUTOFF = 0.3   # alpha-clip threshold the user settled on in Unity; hair is matte, double-sided cutout
SKIN = {"Material154167", "Material154171", "Material154175", "Material154179"}

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
meshes = [o for o in bpy.data.objects if o.type == 'MESH']

# ---- flatten hierarchy
for obj in meshes:
    mw = obj.matrix_world.copy(); obj.parent = None; obj.matrix_world = mw
for e in [o for o in bpy.data.objects if o.type == 'EMPTY']:
    bpy.data.objects.remove(e, do_unlink=True)
for obj in meshes:
    if obj.name.startswith('Obj_Avatar'):
        obj.name = 'Avatar'; obj.data.name = 'Avatar'
    if obj.name == 'Cloth':
        obj.data.name = 'Cloth'

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

# ---- material fixes: bypass "black factor x texture" mixes, blend/cull flags
print("=== MATERIAL FIXES ===")
for mat in bpy.data.materials:
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        continue
    inp = bsdf.inputs['Base Color']
    if inp.is_linked and inp.links[0].from_node.type != 'TEX_IMAGE':
        srcn = inp.links[0].from_node
        stack = [srcn]; seen = set(); tex = None
        while stack:
            n = stack.pop()
            if n.name in seen:
                continue
            seen.add(n.name)
            if n.type == 'TEX_IMAGE':
                tex = n; break
            for i in n.inputs:
                for l in i.links:
                    stack.append(l.from_node)
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
    a = bsdf.inputs['Alpha']
    print(f"{mat.name}: render={mat.surface_render_method} backface_cull={mat.use_backface_culling} alpha_linked={a.is_linked} alpha={a.default_value:.2f}")


# ---- decimate: cloth fully, avatar skin only (via vertex group)
def apply_mod(obj, mod):
    bpy.context.view_layer.objects.active = obj
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier=mod.name)


print("=== DECIMATE ===")
for obj in meshes:
    me = obj.data
    before = len(me.polygons)
    mod = obj.modifiers.new("dec", 'DECIMATE')
    mod.decimate_type = 'COLLAPSE'
    mod.use_collapse_triangulate = True
    if obj.name == 'Cloth':
        mod.ratio = cloth_ratio
    else:
        skin_idx = {i for i, m in enumerate(me.materials) if m and m.name in SKIN}
        vg = obj.vertex_groups.new(name="skin")
        verts = set()
        for p in me.polygons:
            if p.material_index in skin_idx:
                verts.update(p.vertices)
        vg.add(list(verts), 1.0, 'REPLACE')
        mod.ratio = skin_ratio
        mod.vertex_group = "skin"
        mod.vertex_group_factor = 1.0
    apply_mod(obj, mod)
    print(f"{obj.name}: {before} -> {len(me.polygons)} tris")

# ---- downscale textures and re-encode them to real files, so both exporters embed the small versions
# (the glTF exporter copies packed originals byte-for-byte and ignores in-memory scaling)
tex_dir = os.path.join(out_dir, "_tex")
os.makedirs(tex_dir, exist_ok=True)
# images used by the hair materials get a hard alpha (1 above the cutoff, 0 below): AR Quick Look keeps
# blending pixels that pass opacityThreshold at their own alpha, which made the hair look see-through
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
    t = 2048 if max(w, h) >= 3000 else (1024 if max(w, h) > 1024 else None)
    if t:
        img.scale(int(w * t / max(w, h)), int(h * t / max(w, h)))
    nw, nh = img.size
    fmt = 'PNG' if img.file_format == 'PNG' else 'JPEG'
    ext = '.png' if fmt == 'PNG' else '.jpg'
    new = bpy.data.images.new(img.name + "_web", nw, nh, alpha=(img.channels == 4))
    new.colorspace_settings.name = img.colorspace_settings.name
    new.alpha_mode = img.alpha_mode
    px = np.empty(nw * nh * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    hard_alpha = img.name in hair_images and img.channels == 4
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
    print(f"{img.name}: {w}x{h} -> {nw}x{nh} {fmt} {os.path.getsize(path)} bytes colorspace={new.colorspace_settings.name}{' HARD-ALPHA(hair)' if hard_alpha else ''}")
    bpy.data.images.remove(img)

# ---- face direction (eyes vs head) for the poster camera
av = next(o for o in meshes if o.name == 'Avatar')
me = av.data
verts = np.empty(len(me.vertices) * 3, dtype=np.float32); me.vertices.foreach_get("co", verts); verts = verts.reshape(-1, 3)


def centroid(matname):
    idx = next((i for i, m in enumerate(me.materials) if m and m.name == matname), None)
    if idx is None:
        return None
    vs = set()
    for p in me.polygons:
        if p.material_index == idx:
            vs.update(p.vertices)
    return verts[list(vs)].mean(axis=0)


eyes, head = centroid("Material154183"), centroid("Material154167")
if eyes is not None and head is not None:
    fwd = Vector((float(eyes[0] - head[0]), float(eyes[1] - head[1]), 0.0)).normalized()
else:
    fwd = Vector((0.0, -1.0, 0.0))
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

# ---- USD: export a plain .usdc + textures, patch it with pxr (usd_fix.py), then package as .usdz
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

# ---- poster render
scene = bpy.context.scene
engines = [i.identifier for i in scene.render.bl_rna.properties['engine'].enum_items]
scene.render.engine = next((e for e in engines if 'EEVEE' in e), engines[0])
print("render engine:", scene.render.engine)
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

# ---- post-pass on the GLB json: enforce hair flags, report
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
print("meshes:", [m["name"] for m in js["meshes"]], "total tris:", tris)
for m in js["materials"]:
    pbr = m.get("pbrMetallicRoughness", {})
    print(f"  {m['name']}: alpha={m.get('alphaMode', 'OPAQUE')} cutoff={m.get('alphaCutoff')} ds={m.get('doubleSided', False)} bcf={pbr.get('baseColorFactor')} rough={pbr.get('roughnessFactor')} spec={m.get('extensions', {}).get('KHR_materials_specular')} bcTex={pbr.get('baseColorTexture', {}).get('index')} nm={m.get('normalTexture', {}).get('index')}")
for i, im in enumerate(js.get("images", [])):
    bv = js["bufferViews"][im["bufferView"]]
    print(f"  image{i} {im.get('mimeType')} {bv['byteLength']} bytes")
print("FINAL GLB", os.path.getsize(glb_path))
