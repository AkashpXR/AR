"""Bake topstitch meshes that were too heavy to import into the fabric textures.

CLO exports every stitch as geometry ("BindedTrim" nodes). Garment 6 has 5,574 embroidered motifs made of
24.6M triangles: they cannot be loaded, let alone shipped, but without them the eyelet top is plain white.
This reads the trim vertices straight from the original .glb with numpy, drops each one onto the nearest
fabric face (Blender BVH), maps it through that face's UVs and paints the stitch colour into a new texture
per fabric material. CLO's UVs are the flat pattern pieces in millimetres, so that texture is a unique
non-overlapping atlas of the material's pieces; it is first filled with the material's tiled weave texture
(sampled through the KHR_texture_transform mapping) and then replaces both the texture and the mapping.

Called from glb_to_web.py after the transforms and the model scale are baked into the vertices, before the
UV transforms are applied: bake(src_glb, cloth_meshes, transforms, model_scale, keep_2048) -> report lines,
where `transforms` (material name -> (loc_x, loc_y, rot_z, scale_x, scale_y)) is updated in place with the
atlas mapping for every baked material and `keep_2048` receives the atlas image names.
"""
import json, math, os, struct, tempfile
import numpy as np

MAX_POINTS = 2_000_000       # trim vertices sampled (the rest are skipped evenly)
SNAP = 0.006                 # m: a trim vertex farther than this from any fabric is ignored
STITCH_WIDTH_MM = 0.5        # painted line width in pattern millimetres
PX_PER_MM = 4.0              # atlas resolution, capped at 2048 px per side
MIN_HITS = 200               # materials with fewer stitch samples are left alone

_CTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _read_glb(path):
    with open(path, "rb") as f:
        f.read(12)
        clen, _ = struct.unpack("<II", f.read(8))
        js = json.loads(f.read(clen))
        blen, _ = struct.unpack("<II", f.read(8))
        bin_ = f.read(blen)
    return js, bin_


def _accessor(js, bin_, idx):
    a = js["accessors"][idx]
    bv = js["bufferViews"][a["bufferView"]]
    dt = np.dtype(_CTYPE[a["componentType"]]); n = _NCOMP[a["type"]]
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride", 0)
    if stride and stride != dt.itemsize * n:
        return np.ndarray(shape=(a["count"], n), dtype=dt, buffer=bin_, offset=start, strides=(stride, dt.itemsize))
    return np.frombuffer(bin_, dtype=dt, count=a["count"] * n, offset=start).reshape(a["count"], n)


def _node_matrices(js):
    """World matrix (glTF space) of every node."""
    nodes = js["nodes"]
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i
    cache = {}

    def local(n):
        if "matrix" in n:
            return np.array(n["matrix"], dtype=np.float64).reshape(4, 4).T
        m = np.eye(4)
        s = n.get("scale"); r = n.get("rotation"); t = n.get("translation")
        if s:
            m = m @ np.diag([s[0], s[1], s[2], 1.0])
        if r:
            x, y, z, w = r
            rm = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
                           [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
                           [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
                           [0, 0, 0, 1]], dtype=np.float64)
            m = rm @ m
        if t:
            tm = np.eye(4); tm[:3, 3] = t
            m = tm @ m
        return m

    def world(i):
        if i not in cache:
            cache[i] = (world(parent[i]) @ local(nodes[i])) if i in parent else local(nodes[i])
        return cache[i]
    return {i: world(i) for i in range(len(nodes))}


def _image_mean(js, bin_, tex_index, cache):
    """Mean colour (display space, 0..1) of a glTF texture, via a temporary Blender image."""
    import bpy
    if tex_index is None:
        return np.array([1.0, 1.0, 1.0])
    if tex_index in cache:
        return cache[tex_index]
    img = js["images"][js["textures"][tex_index]["source"]]
    mean = np.array([1.0, 1.0, 1.0])
    if "bufferView" in img:
        bv = js["bufferViews"][img["bufferView"]]
        data = bin_[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]
        ext = ".png" if "png" in img.get("mimeType", "") else ".jpg"
        path = os.path.join(tempfile.gettempdir(), f"stitch_tex_{tex_index}{ext}")
        with open(path, "wb") as f:
            f.write(data)
        try:
            bi = bpy.data.images.load(path)
            w, h = bi.size
            if w and h:
                px = np.empty(w * h * 4, dtype=np.float32); bi.pixels.foreach_get(px)
                px = px.reshape(-1, 4)
                a = px[:, 3:4]
                mean = (px[:, :3] * a).sum(axis=0) / max(float(a.sum()), 1.0)
            bpy.data.images.remove(bi)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    cache[tex_index] = mean
    return mean


def _base_color_tex_node(mat):
    """The image texture node feeding Base Color (through mix/multiply nodes), or None."""
    if not mat or not mat.node_tree:
        return None
    bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None or not bsdf.inputs['Base Color'].is_linked:
        return None
    stack = [bsdf.inputs['Base Color'].links[0].from_node]; seen = set()
    while stack:
        n = stack.pop()
        if n.name in seen:
            continue
        seen.add(n.name)
        if n.type == 'TEX_IMAGE':
            return n if n.image is not None else None
        for i in n.inputs:
            for l in i.links:
                stack.append(l.from_node)
    return None


def bake(src_glb, cloth_meshes, transforms, model_scale, keep_2048):
    import bpy
    from mathutils.bvhtree import BVHTree
    out = []
    js, bin_ = _read_glb(src_glb)
    mats_w = _node_matrices(js)
    trims = [(i, n) for i, n in enumerate(js["nodes"]) if str(n.get("name", "")).startswith("BindedTrim") and "mesh" in n]
    if not trims:
        return ["no BindedTrim nodes in the source"]
    total = sum(js["accessors"][p["attributes"]["POSITION"]]["count"] for _, n in trims for p in js["meshes"][n["mesh"]]["primitives"])
    step = max(1, int(math.ceil(total / MAX_POINTS)))
    gltf_to_blender = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=np.float64)   # Y-up -> Z-up

    # ---- trim vertices (Blender space, scaled) with their stitch colour (linear)
    pts, cols = [], []
    tex_cache = {}; mat_col = {}
    for i, n in trims:
        for p in js["meshes"][n["mesh"]]["primitives"]:
            mi = p.get("material")
            if mi not in mat_col:
                m = js["materials"][mi] if mi is not None else {}
                pbr = m.get("pbrMetallicRoughness", {})
                fac = np.array(pbr.get("baseColorFactor", [1, 1, 1, 1])[:3], dtype=np.float64)
                tmean = _image_mean(js, bin_, pbr.get("baseColorTexture", {}).get("index"), tex_cache)
                mat_col[mi] = fac * np.power(np.clip(tmean, 0, 1), 2.2)   # linear
            pos = _accessor(js, bin_, p["attributes"]["POSITION"]).astype(np.float64)[::step]
            m = mats_w[i]
            pos = pos @ m[:3, :3].T + m[:3, 3]
            pos = (pos @ gltf_to_blender.T) * model_scale
            pts.append(pos.astype(np.float32)); cols.append(np.repeat(mat_col[mi][None, :], len(pos), axis=0))
    pts = np.concatenate(pts); cols = np.concatenate(cols)
    out.append(f"{len(trims)} topstitch meshes, {total:,} vertices, sampling every {step} -> {len(pts):,} points")

    # ---- one BVH over all cloth faces, with a face -> (mesh, local face) map
    verts, polys, face_mesh, face_local, base = [], [], [], [], 0
    for mi_, o in enumerate(cloth_meshes):
        me = o.data
        c = np.empty(len(me.vertices) * 3, dtype=np.float32); me.vertices.foreach_get("co", c); c = c.reshape(-1, 3)
        lt = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("loop_total", lt)
        ls = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("loop_start", ls)
        lv = np.empty(len(me.loops), dtype=np.int32); me.loops.foreach_get("vertex_index", lv)
        for fi, (s, t) in enumerate(zip(ls.tolist(), lt.tolist())):
            polys.append(tuple(int(x) + base for x in lv[s:s + t]))
            face_mesh.append(mi_); face_local.append(fi)
        verts += [tuple(v) for v in c.tolist()]; base += len(c)
    if not polys:
        return ["no cloth faces to bake onto"]
    bvh = BVHTree.FromPolygons(verts, polys, all_triangles=False)
    face_mesh = np.array(face_mesh, dtype=np.int32); face_local = np.array(face_local, dtype=np.int32)

    from mathutils import Vector
    hit_face = np.full(len(pts), -1, dtype=np.int64); hit_loc = np.zeros((len(pts), 3), dtype=np.float32)
    for k in range(len(pts)):
        loc, _, idx, dist = bvh.find_nearest(Vector(pts[k]), SNAP)
        if loc is not None:
            hit_face[k] = idx; hit_loc[k] = loc
    ok = hit_face >= 0
    out.append(f"{int(ok.sum()):,} points within {SNAP * 1000:.0f} mm of fabric")
    if not ok.any():
        return out

    # ---- per mesh: barycentric UVs of the hits, grouped by material
    hits = {}   # material name -> list of (uv array, colour array)
    for mi_, o in enumerate(cloth_meshes):
        sel = ok & (face_mesh[np.maximum(hit_face, 0)] == mi_)
        if not sel.any():
            continue
        me = o.data
        if not me.uv_layers:
            continue
        c = np.empty(len(me.vertices) * 3, dtype=np.float32); me.vertices.foreach_get("co", c); c = c.reshape(-1, 3)
        ls = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("loop_start", ls)
        lv = np.empty(len(me.loops), dtype=np.int32); me.loops.foreach_get("vertex_index", lv)
        pm = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("material_index", pm)
        uvl = me.uv_layers[0]
        uv = np.empty(len(me.loops) * 2, dtype=np.float32)
        try:
            uvl.uv.foreach_get("vector", uv)
        except Exception:
            uvl.data.foreach_get("uv", uv)
        uv = uv.reshape(-1, 2)
        fl = face_local[hit_face[sel]]
        l0 = ls[fl]
        A, B, C = c[lv[l0]], c[lv[l0 + 1]], c[lv[l0 + 2]]
        P = hit_loc[sel]
        v0, v1, v2 = B - A, C - A, P - A
        d00 = (v0 * v0).sum(1); d01 = (v0 * v1).sum(1); d11 = (v1 * v1).sum(1); d20 = (v2 * v0).sum(1); d21 = (v2 * v1).sum(1)
        den = np.maximum(d00 * d11 - d01 * d01, 1e-12)
        wb = (d11 * d20 - d01 * d21) / den; wc = (d00 * d21 - d01 * d20) / den; wa = 1 - wb - wc
        huv = wa[:, None] * uv[l0] + wb[:, None] * uv[l0 + 1] + wc[:, None] * uv[l0 + 2]
        for si in np.unique(pm[fl]).tolist():
            mat = me.materials[si] if si < len(me.materials) else None
            if mat is None:
                continue
            m = pm[fl] == si
            hits.setdefault(mat.name, []).append((huv[m], cols[sel][m]))

    # ---- one atlas per material: tiled weave + painted stitches
    for mname, chunks in hits.items():
        huv = np.concatenate([h for h, _ in chunks]); hcol = np.concatenate([c_ for _, c_ in chunks])
        if len(huv) < MIN_HITS:
            continue
        mat = bpy.data.materials[mname]
        tex_node = _base_color_tex_node(mat)
        if tex_node is None:
            continue
        # UV extent of everything that uses this material (all its pattern pieces)
        umin = np.array([np.inf, np.inf]); umax = np.array([-np.inf, -np.inf])
        for o in cloth_meshes:
            me = o.data
            si = next((i for i, m in enumerate(me.materials) if m is not None and m.name == mname), None)
            if si is None or not me.uv_layers:
                continue
            pm = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("material_index", pm)
            lt = np.empty(len(me.polygons), dtype=np.int32); me.polygons.foreach_get("loop_total", lt)
            uv = np.empty(len(me.loops) * 2, dtype=np.float32)
            try:
                me.uv_layers[0].uv.foreach_get("vector", uv)
            except Exception:
                me.uv_layers[0].data.foreach_get("uv", uv)
            uv = uv.reshape(-1, 2)[np.repeat(pm, lt) == si]
            if len(uv):
                umin = np.minimum(umin, uv.min(0)); umax = np.maximum(umax, uv.max(0))
        ext = np.maximum(umax - umin, 1e-6)
        pad = ext * 0.01
        umin -= pad; umax += pad; ext = umax - umin
        px_per_unit = min(PX_PER_MM, 2048 / ext.max())
        res = np.clip(np.ceil(ext * px_per_unit).astype(int), 64, 2048)
        W, H = int(res[0]), int(res[1])
        # fill with the tiled source texture through the material's mapping
        lx, ly, rz, sx, sy = transforms.get(mname, (0.0, 0.0, 0.0, 1.0, 1.0))
        src_img = tex_node.image
        sw, sh = src_img.size
        spx = np.empty(sw * sh * 4, dtype=np.float32); src_img.pixels.foreach_get(spx); spx = spx.reshape(sh, sw, 4)
        gu = umin[0] + (np.arange(W) + 0.5) / W * ext[0]
        gv = umin[1] + (np.arange(H) + 0.5) / H * ext[1]
        U, V = np.meshgrid(gu, gv)
        tu, tv = U * sx, V * sy
        if abs(rz) > 1e-6:
            cs, sn = math.cos(rz), math.sin(rz)
            tu, tv = cs * tu - sn * tv, sn * tu + cs * tv
        tu = (tu + lx) % 1.0; tv = (tv + ly) % 1.0
        ix = np.clip((tu * sw).astype(int), 0, sw - 1); iy = np.clip((tv * sh).astype(int), 0, sh - 1)
        atlas = spx[iy, ix].copy()
        # stitches: the material's base-colour factor (the multiply node) is applied at render time, so divide it out
        fac = np.array([1.0, 1.0, 1.0])
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        inp = bsdf.inputs['Base Color'] if bsdf is not None else None
        if inp is not None and inp.is_linked and inp.links[0].from_node.type in ('MIX', 'MIX_RGB'):
            for s_ in inp.links[0].from_node.inputs:
                if s_.type == 'RGBA' and not s_.is_linked:
                    fac = np.array(s_.default_value[:3], dtype=np.float64)
        col = np.clip(hcol / np.maximum(fac, 0.05), 0, 1)
        col_disp = np.power(col, 1 / 2.2)
        px = ((huv - umin) / ext * np.array([W, H])).astype(int)
        r = int(max(1, min(3, round(STITCH_WIDTH_MM * px_per_unit / 2))))
        offs = [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1) if dx * dx + dy * dy <= r * r]
        for dx, dy in offs:
            xs = np.clip(px[:, 0] + dx, 0, W - 1); ys = np.clip(px[:, 1] + dy, 0, H - 1)
            atlas[ys, xs, :3] = col_disp
        atlas[:, :, 3] = 1.0
        name = f"stitch_atlas_{mname}"
        img = bpy.data.images.new(name, W, H, alpha=False)
        img.colorspace_settings.name = src_img.colorspace_settings.name
        img.pixels.foreach_set(atlas.ravel().astype(np.float32))
        img.file_format = 'PNG'
        img.pack()
        for n in mat.node_tree.nodes:
            if n.type == 'TEX_IMAGE' and n.image == src_img:
                n.image = img
                n.extension = 'EXTEND'
        transforms[mname] = (float(-umin[0] / ext[0]), float(-umin[1] / ext[1]), 0.0, float(1.0 / ext[0]), float(1.0 / ext[1]))
        if max(W, H) > 1024:
            keep_2048.add(name)
        out.append(f"{mname}: {len(huv):,} stitch samples painted into a {W}x{H} atlas (pattern extent {ext[0]:.0f}x{ext[1]:.0f} units, colour {np.round(col.mean(0), 2).tolist()})")
    return out
