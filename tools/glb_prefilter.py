"""Pre-filter a CLO .glb before Blender loads it.

Some exports carry thousands of "BindedTrim" topstitch meshes (garment 6: 5,574 meshes, 24.6M triangles,
767 MB) that Blender cannot import within sane memory. When the trims exceed the limits, this writes a
copy of the file with those nodes removed from the scene so Blender never instantiates them.

usage (module): filtered_path, info = prefilter(src_glb, out_glb, max_trims=400, max_trim_tris=2_000_000)
usage (cli):    python glb_prefilter.py <in.glb> <out.glb>
"""
import json, os, struct, sys


def read_glb(path):
    with open(path, "rb") as f:
        magic, ver, length = struct.unpack("<III", f.read(12))
        clen, ctype = struct.unpack("<II", f.read(8))
        js = json.loads(f.read(clen))
        rest = f.read()
    return magic, ver, js, rest


def write_glb(path, magic, ver, js, rest):
    jb = json.dumps(js, separators=(",", ":")).encode("utf-8")
    jb += b" " * ((4 - len(jb) % 4) % 4)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", magic, ver, 12 + 8 + len(jb) + len(rest)))
        f.write(struct.pack("<II", len(jb), 0x4E4F534A))
        f.write(jb)
        f.write(rest)


def trim_stats(js):
    acc = js["accessors"]
    count = 0; tris = 0
    for n in js["nodes"]:
        if str(n.get("name", "")).startswith("BindedTrim") and "mesh" in n:
            count += 1
            tris += sum(acc[pr["indices"]]["count"] // 3 for pr in js["meshes"][n["mesh"]]["primitives"] if "indices" in pr)
    return count, tris


def prefilter(src, dst, max_trims=400, max_trim_tris=2_000_000):
    magic, ver, js, rest = read_glb(src)
    count, tris = trim_stats(js)
    info = {"trims": count, "trim_tris": tris, "stripped": False}
    if count <= max_trims and tris <= max_trim_tris:
        return src, info
    drop = {i for i, n in enumerate(js["nodes"]) if str(n.get("name", "")).startswith("BindedTrim") and "mesh" in n}
    for n in js["nodes"]:
        if "children" in n:
            n["children"] = [c for c in n["children"] if c not in drop]
    for sc in js.get("scenes", []):
        sc["nodes"] = [c for c in sc.get("nodes", []) if c not in drop]
    for i in drop:   # keep indices stable, just detach the mesh so nothing gets instantiated
        js["nodes"][i].pop("mesh", None)
    write_glb(dst, magic, ver, js, rest)
    info["stripped"] = True
    return dst, info


if __name__ == "__main__":
    out, info = prefilter(sys.argv[1], sys.argv[2])
    print(info, "->", out)
