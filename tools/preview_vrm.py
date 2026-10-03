# Import a .vrm back into Blender and render quick QA previews:
#   blender -b --python preview_vrm.py -- model.vrm out_prefix
# writes <out_prefix>_tpose.png (front+side) and <out_prefix>_pose.png (test pose via humanoid bones)
import bpy, sys, math, os
from mathutils import Vector, Euler

argv = sys.argv[sys.argv.index("--") + 1:]
vrm, prefix = argv[0], argv[1]
bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.import_scene.vrm(filepath=vrm)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
hb = arm.data.vrm_addon_extension.vrm1.humanoid.human_bones

scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
sh = scene.display.shading
sh.light = "FLAT"
sh.color_type = "TEXTURE"
scene.render.resolution_x, scene.render.resolution_y = 1400, 1000
scene.render.film_transparent = False
scene.world = bpy.data.worlds.new("w") if not scene.world else scene.world
scene.world.color = (0.18, 0.18, 0.2)

def bbox():
    pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi

cam_data = bpy.data.cameras.new("cam")
cam_data.type = "ORTHO"
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam

def shoot(path, side=False):
    bpy.context.view_layer.update()
    lo, hi = bbox()
    c = (lo + hi) / 2
    aspect = scene.render.resolution_x / scene.render.resolution_y
    size = max(hi.x - lo.x, hi.y - lo.y, (hi.z - lo.z) * aspect) * 1.05
    cam_data.ortho_scale = size
    if side:
        cam.location = (c.x + 5, c.y, c.z)
        cam.rotation_euler = Euler((math.pi / 2, 0, math.pi / 2))
    else:
        cam.location = (c.x, c.y - 5, c.z)
        cam.rotation_euler = Euler((math.pi / 2, 0, 0))
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)

def pb(name):
    bn = getattr(hb, name).node.bone_name
    return arm.pose.bones.get(bn) if bn else None

shoot(prefix + "_front.png")
shoot(prefix + "_side.png", side=True)

# test pose: rotate in each bone's local space (VRM rest is T-pose, so this exercises weights)
def rot(name, axis, deg):
    b = pb(name)
    if b:
        b.rotation_mode = "XYZ"
        e = list(b.rotation_euler)
        e["XYZ".index(axis)] += math.radians(deg)
        b.rotation_euler = e

# world-space helpers via armature-space rotation
from mathutils import Matrix, Quaternion
def rot_world(name, axis_vec, deg):
    b = pb(name)
    if not b:
        return
    bpy.context.view_layer.update()
    m = b.matrix.copy()
    q = Quaternion(Vector(axis_vec), math.radians(deg))
    loc = m.translation.copy()
    r = q.to_matrix().to_4x4() @ m
    r.translation = loc
    b.matrix = r
    bpy.context.view_layer.update()

rot_world("left_upper_arm", (0, 1, 0), 60)     # arms down-ish
rot_world("right_upper_arm", (0, 1, 0), -60)
rot_world("left_lower_arm", (0, 0, 1), 60)     # elbows bent forward
rot_world("right_lower_arm", (0, 0, 1), -60)
rot_world("left_upper_leg", (1, 0, 0), -45)    # left thigh forward
rot_world("left_lower_leg", (1, 0, 0), 70)     # knee bend
rot_world("head", (0, 0, 1), 25)
rot_world("spine", (1, 0, 0), -10)
shoot(prefix + "_pose.png")
shoot(prefix + "_pose_side.png", side=True)
print("PREVIEW_OK")
