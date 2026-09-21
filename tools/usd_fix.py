"""Patch a Blender-exported USD for AR Quick Look and package it as .usdz.

- hair materials: opacityThreshold (alpha cutout), roughness 1, metallic 0, black specular (specular workflow)
- every mesh: doubleSided, so thin garment shells do not vanish when viewed from inside/below
- packages with UsdUtils.CreateNewUsdzPackage (correct alignment, textures bundled)

Runs inside Blender (bundled pxr) or with system Python (pip install usd-core).
usage: python usd_fix.py <usd_dir containing model.usdc> <out.usdz> [cutoff] [hair_material_name ...]
"""
import os, sys, shutil

from pxr import Usd, UsdGeom, UsdShade, Sdf, UsdUtils


def fix_and_package(usd_dir, usdz_path, cutoff=0.3, hair_names=()):
    usdc = os.path.join(usd_dir, "model.usdc")
    stage = Usd.Stage.Open(usdc)
    report = []
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            UsdGeom.Mesh(prim).CreateDoubleSidedAttr().Set(True)
            report.append(f"mesh {prim.GetName()}: doubleSided")
        if prim.IsA(UsdShade.Shader):
            sh = UsdShade.Shader(prim)
            if sh.GetIdAttr().Get() != "UsdPreviewSurface":
                continue
            mat_name = prim.GetParent().GetName()
            if mat_name not in hair_names:
                continue
            sh.CreateInput("opacityThreshold", Sdf.ValueTypeNames.Float).Set(float(cutoff))
            sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(1.0)
            sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
            sh.CreateInput("useSpecularWorkflow", Sdf.ValueTypeNames.Int).Set(1)
            sh.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set((0.0, 0.0, 0.0))
            op = sh.GetInput("opacity")
            connected = bool(op and op.HasConnectedSource())
            report.append(f"hair {mat_name}: opacityThreshold={cutoff} opacity_connected={connected}")
            if not connected:
                report.append(f"WARNING hair {mat_name}: opacity is not driven by the texture alpha")
    stage.GetRootLayer().Save()
    if os.path.exists(usdz_path):
        os.remove(usdz_path)
    ok = UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(usdc), usdz_path)
    report.append(f"usdz packaged ok={ok} bytes={os.path.getsize(usdz_path) if os.path.exists(usdz_path) else 0}")
    # verify the package
    z = Usd.Stage.Open(usdz_path)
    rng = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]).ComputeWorldBound(z.GetPseudoRoot()).ComputeAlignedRange()
    size = rng.GetMax() - rng.GetMin()
    report.append(f"usdz bounds size={[round(v, 3) for v in size]} upAxis={UsdGeom.GetStageUpAxis(z)} metersPerUnit={UsdGeom.GetStageMetersPerUnit(z)}")
    return report


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    usd_dir, usdz_path = sys.argv[1], sys.argv[2]
    cutoff = float(sys.argv[3]) if len(sys.argv) > 3 else 0.3
    hair = set(sys.argv[4:])
    for line in fix_and_package(usd_dir, usdz_path, cutoff, hair):
        print(line)
