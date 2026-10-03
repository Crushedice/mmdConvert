# Dev: dump MMD rigid bodies (shape, size, transform, collision filter) of a PMX
#   blender -b --python dump_rigid.py -- model.pmx [bone-substring ...]
import bpy, sys
argv = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.mmd_tools.import_model(filepath=argv[0], scale=0.08, types={"MESH", "ARMATURE", "PHYSICS"},
                               log_level="ERROR")
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
bpy.context.view_layer.update()
for o in bpy.data.objects:
    if getattr(o, "mmd_type", "") != "RIGID_BODY":
        continue
    c = o.constraints.get("mmd_tools_rigid_parent")
    bone = c.subtarget if c else ""
    if len(argv) > 1 and not any(s in bone or s in o.mmd_rigid.name_j for s in argv[1:]):
        continue
    r = o.mmd_rigid
    bb = [tuple(v) for v in o.bound_box]
    dims = [round(max(b[i] for b in bb) - min(b[i] for b in bb), 3) for i in range(3)]
    loc, rot, sc = o.matrix_world.decompose()
    print("RB", r.name_j, "| bone", bone, "| type", r.type, "| shape", r.shape, "| size", [round(x, 3) for x in r.size],
          "| scale", [round(x, 3) for x in sc], "| bbox", dims, "| loc", [round(x, 3) for x in loc],
          "| grp", r.collision_group_number, "| nocollide", [i for i, m in enumerate(r.collision_group_mask) if m],
          "| verts", len(o.data.vertices))
