# Blender headless: import a PMX with mmd_tools and dump structure for debugging.
# blender -b --factory-startup? (no: we need user addons) -> blender -b --python inspect_pmx.py -- model.pmx
import bpy, sys, json, os

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
pmx = argv[0]
out = argv[1] if len(argv) > 1 else None

bpy.ops.wm.read_factory_settings(use_empty=True) if False else None
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)

bpy.ops.mmd_tools.import_model(filepath=pmx, scale=0.08, types={"MESH", "ARMATURE", "PHYSICS", "MORPHS", "DISPLAY"},
                               clean_model=True, rename_bones=True, dictionary="INTERNAL", log_level="WARNING")
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
info = {"objects": [(o.name, o.type, getattr(o, "mmd_type", ""), o.parent.name if o.parent else None) for o in bpy.data.objects if getattr(o, "mmd_type", "") not in ("RIGID_BODY", "JOINT")],
        "rigid_count": sum(1 for o in bpy.data.objects if getattr(o, "mmd_type", "") == "RIGID_BODY"),
        "bones": []}
vg = {}
for m in (o for o in bpy.data.objects if o.type == "MESH" and o.find_armature() == arm):
    import collections
    cnt = collections.Counter()
    for v in m.data.vertices:
        for g in v.groups:
            if g.weight > 0.001:
                cnt[m.vertex_groups[g.group].name] += 1
    for k, c in cnt.items():
        vg[k] = vg.get(k, 0) + c
    info.setdefault("meshes", []).append({"name": m.name, "verts": len(m.data.vertices),
        "shape_keys": [k.name for k in m.data.shape_keys.key_blocks] if m.data.shape_keys else [],
        "materials": [s.material.name if s.material else None for s in m.material_slots]})
for pb in arm.pose.bones:
    b = pb.bone
    info["bones"].append({"n": b.name, "j": pb.mmd_bone.name_j, "p": b.parent.name if b.parent else None,
        "deform": b.use_deform, "w": vg.get(b.name, 0), "c": [c.type for c in pb.constraints],
        "h": [round(x, 3) for x in b.head_local], "t": [round(x, 3) for x in b.tail_local]})
s = json.dumps(info, ensure_ascii=False, indent=0)
if out:
    open(out, "w", encoding="utf-8").write(s)
else:
    print(s)
