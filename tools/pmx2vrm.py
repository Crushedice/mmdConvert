# pmx2vrm.py - convert an MMD model (.pmx/.pmd) into a VRM 1.0 avatar, headless.
#
#   blender.exe -b --python pmx2vrm.py -- <input.pmx> <output.vrm> [options]
#
# Requires the Blender extensions "mmd_tools" and "VRM format" (VRM Add-on for Blender)
# to be installed and enabled in the user preferences.
#
# Pipeline
#   1. import PMX with mmd_tools (scale 0.08 -> metres)
#   2. strip MMD rig machinery (IK, additional-transform constraints, _dummy_/_shadow_ bones)
#   3. merge D-bones (足D/ひざD/足首D) into the FK leg bones, add a real "Hips" bone
#   4. map VRM humanoid bones from the standard Japanese bone names
#   5. convert MMD rigid bodies -> VRM spring bones + colliders
#   6. MMD morphs -> VRM expressions (presets + every other shape key as custom)
#   7. MMD materials -> MToon 1.0 (texture, toon shade colour, alpha mode, edge -> outline)
#   8. drop physics objects / invisible materials, fill meta, validate, export
#      (the VRM add-on converts the A-pose to a T-pose automatically on export)

import bpy
import sys
import os
import re
import json
import argparse
import traceback
from mathutils import Vector

# ----------------------------------------------------------------------------- args
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ap = argparse.ArgumentParser(prog="pmx2vrm")
ap.add_argument("input")
ap.add_argument("output", nargs="?")
ap.add_argument("--scale", type=float, default=0.08, help="PMX->metre scale (default 0.08)")
ap.add_argument("--author", default="", help="meta author (default: taken from PMX comment or 'Unknown')")
ap.add_argument("--name", default="", help="meta avatar name (default: PMX model name)")
ap.add_argument("--no-spring", action="store_true", help="don't convert rigid bodies to spring bones")
ap.add_argument("--no-outline", action="store_true", help="don't convert MMD edges to MToon outlines")
ap.add_argument("--outline-scale", type=float, default=1.0, help="multiplier for outline width")
ap.add_argument("--keep-hidden", action="store_true", help="keep faces of fully transparent (alpha 0) materials")
ap.add_argument("--no-custom-expr", action="store_true", help="don't expose non-preset morphs as custom expressions")
ap.add_argument("--no-blend", action="store_true", help="don't save the prepared .blend next to the .vrm")
ap.add_argument("--no-export", action="store_true", help="prepare + save .blend only")
ap.add_argument("--dense-shapekeys", action="store_true",
                help="write shape keys as dense accessors (bigger file; only for very old loaders)")
args = ap.parse_args(argv)

src = os.path.abspath(args.input)
out = os.path.abspath(args.output) if args.output else os.path.splitext(src)[0] + ".vrm"
os.makedirs(os.path.dirname(out), exist_ok=True)
log_lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    log_lines.append(s)
    print("[pmx2vrm]", s, flush=True)


# ----------------------------------------------------------------------------- helpers
def vrm_mod():
    for name in list(sys.modules):
        if re.fullmatch(r"bl_ext\.[^.]+\.vrm", name):
            return name
    raise RuntimeError("VRM add-on not loaded - enable 'VRM format' in Blender preferences")


def set_active(obj, mode="OBJECT"):
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    if mode != "OBJECT":
        bpy.ops.object.mode_set(mode=mode)


def jname_map(arm):
    m = {}
    for pb in arm.pose.bones:
        j = getattr(pb.mmd_bone, "name_j", "") if hasattr(pb, "mmd_bone") else ""
        if j and j not in m:
            m[j] = pb.name
    return m


def weights_by_group(meshes):
    tot = {}
    for m in meshes:
        names = [g.name for g in m.vertex_groups]
        for v in m.data.vertices:
            for g in v.groups:
                if g.weight > 1e-4:
                    n = names[g.group]
                    tot[n] = tot.get(n, 0) + 1
    return tot


def merge_vertex_groups(meshes, mapping):
    """mapping: src_group -> dst_group. Adds src weights to dst and removes src."""
    for m in meshes:
        vgs = m.vertex_groups
        pairs = [(vgs[s], d) for s, d in mapping.items() if s in vgs]
        if not pairs:
            continue
        for s, d in pairs:
            if d not in vgs:
                vgs.new(name=d)
        idx = {s.index: vgs[d] for s, d in pairs}
        for v in m.data.vertices:
            for g in v.groups:
                dst = idx.get(g.group)
                if dst is not None and g.weight > 0:
                    dst.add([v.index], g.weight, "ADD")
        for s, _ in pairs:
            vgs.remove(s)


# ----------------------------------------------------------------------------- 1 import
log("input :", src)
log("output:", out)
bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.mmd_tools.import_model(
    filepath=src, scale=args.scale,
    types={"MESH", "ARMATURE", "PHYSICS", "MORPHS", "DISPLAY"},
    clean_model=True, remove_doubles=False, fix_ik_links=False,
    rename_bones=True, use_underscore=False, dictionary="DISABLED",
    log_level="WARNING")

root = next((o for o in bpy.data.objects if getattr(o, "mmd_type", "") == "ROOT"), None)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
meshes = [o for o in bpy.data.objects if o.type == "MESH" and getattr(o, "mmd_type", "NONE") == "NONE"
          and any(md.type == "ARMATURE" and md.object == arm for md in o.modifiers)]
model_name = args.name or (root.mmd_root.name if root else "") or os.path.splitext(os.path.basename(src))[0]
model_comment = root.mmd_root.comment_text if root and hasattr(root.mmd_root, "comment_text") else ""
log(f"model '{model_name}': {len(arm.data.bones)} bones, {len(meshes)} meshes, "
    f"{sum(len(m.data.vertices) for m in meshes)} verts")

# mmd_tools stores SDEF data as helper shape keys (mmd_sdef_*) - not real morphs, drop them
for m in meshes:
    sk = m.data.shape_keys
    if sk:
        for kb in [kb for kb in sk.key_blocks if kb.name.startswith("mmd_sdef")]:
            m.shape_key_remove(kb)

# kill drivers (mmd_tools morph/SDEF helpers) so nothing fights our edits
for o in [arm] + meshes:
    o.animation_data_clear()
    if o.type == "MESH" and o.data.shape_keys:
        o.data.shape_keys.animation_data_clear()
arm.data.animation_data_clear() if arm.data.animation_data else None

# ----------------------------------------------------------------------------- 2 strip rig machinery
J = jname_map(arm)
ik_targets = set()
for pb in arm.pose.bones:
    for c in pb.constraints:
        if c.type == "IK":
            if c.subtarget:
                ik_targets.add(c.subtarget)
            if getattr(c, "pole_subtarget", ""):
                ik_targets.add(c.pole_subtarget)
    for c in list(pb.constraints):
        pb.constraints.remove(c)
    pb.location = (0, 0, 0)
    pb.rotation_quaternion = (1, 0, 0, 0)
    pb.rotation_euler = (0, 0, 0)
    pb.scale = (1, 1, 1)

W = weights_by_group(meshes)

# rigid bodies -> which bones they are attached to (so we never delete those)
rigid_objs = [o for o in bpy.data.objects if getattr(o, "mmd_type", "") == "RIGID_BODY"]
def rb_bone(o):
    c = o.constraints.get("mmd_tools_rigid_parent")
    return c.subtarget if c and c.target == arm else ""
rigid_bones = {rb_bone(o) for o in rigid_objs} - {""}

set_active(arm, "EDIT")
eb = arm.data.edit_bones


def subtree(b):
    yield b
    for c in b.children:
        yield from subtree(c)


def subtree_is_dead(b):
    return all(W.get(x.name, 0) == 0 and x.name not in rigid_bones for x in subtree(b))


removed = []
# mmd_tools helper bones
for b in [b for b in eb if b.name.startswith(("_dummy_", "_shadow_"))]:
    removed.append(b.name)
    eb.remove(b)
# IK targets + their weightless subtrees, then weightless IK-named leftovers (足IK親 ...)
for n in ik_targets:
    b = eb.get(n)
    if b and subtree_is_dead(b):
        for x in reversed(list(subtree(b))):
            removed.append(x.name)
            eb.remove(x)
ik_pat = re.compile(r"(IK|ＩＫ)")
changed = True
while changed:
    changed = False
    for b in list(eb):
        pbj = next((j for j, n in J.items() if n == b.name), b.name)
        if ik_pat.search(pbj) and not b.children and subtree_is_dead(b):
            removed.append(b.name)
            eb.remove(b)
            changed = True

# D-bones: any "<name>D" sitting on top of "<name>" (足D, ひざD, 足首D, 腕D, ひじD, ...) is a
# deform duplicate driven by constraints. Merge its weights into the base bone and hang its
# children (twist bones etc.) under the base, so everything follows the humanoid bone.
d_map = {}
for j, dn in J.items():
    if len(j) > 1 and j[-1] in "DＤ":
        bn = J.get(j[:-1])
        if bn and bn != dn and eb.get(bn) and eb.get(dn) and (eb[bn].head - eb[dn].head).length < 0.01:
            d_map[dn] = bn
for dn, bn in d_map.items():
    for c in list(eb[dn].children):
        if c.name not in d_map:
            c.use_connect = False
            c.parent = eb[bn]
for dn in d_map:
    removed.append(dn)
    eb.remove(eb[dn])

# Hips: MMD has 上半身 and 下半身 as siblings; VRM wants spine under hips.
lower, upper = J.get("下半身"), J.get("上半身")
legL, legR = J.get("左足"), J.get("右足")
HIPS = None
if upper and eb.get(upper) and legL and legR and eb.get(legL) and eb.get(legR):
    ub = eb[upper]
    mid = (eb[legL].head + eb[legR].head) / 2
    hips = eb.new("Hips")
    hips.head = Vector((0.0, mid.y, mid.z + (ub.head.z - mid.z) * 0.3))
    hips.tail = Vector((0.0, ub.head.y, ub.head.z))
    if (hips.tail - hips.head).length < 1e-4:
        hips.tail = hips.head + Vector((0, 0, 0.05))
    anchor = eb[lower] if lower and eb.get(lower) else ub
    hips.parent = anchor.parent
    hips.use_deform = False
    move = [b for b in (eb.get(lower) if lower else None, ub) if b]
    if not lower:  # PMD-style rig: legs hang from センター/腰 directly
        for leg in (eb[legL], eb[legR]):
            top = leg
            while top.parent and top.parent != anchor.parent:
                top = top.parent
            if top.parent == anchor.parent:
                move.append(top)
    for b in move:
        b.use_connect = False
        b.parent = hips
    HIPS = "Hips"
bpy.ops.object.mode_set(mode="OBJECT")

merge_vertex_groups(meshes, d_map)
for o in rigid_objs:  # re-point rigid bodies that sat on a merged D-bone
    c = o.constraints.get("mmd_tools_rigid_parent")
    if c and c.subtarget in d_map:
        c.subtarget = d_map[c.subtarget]
log(f"removed {len(removed)} helper/IK/D bones, merged D-bones: {list(d_map.items())}")
J = jname_map(arm)

# ----------------------------------------------------------------------------- 3 VRM humanoid
VRM = vrm_mod()
ext = arm.data.vrm_addon_extension
ext.spec_version = "1.0"
# stop the add-on's migration handler from re-running its own auto-assignment over ours
ext.vrm1.humanoid.human_bones.initial_automatic_bone_assignment = False
ext.vrm1.expressions.initial_automatic_expression_assignment = False
ext.spring_bone1.initial_automatic_spring_bone_assignment = False
ext.vrm0.humanoid.initial_automatic_bone_assignment = False
hb = ext.vrm1.humanoid.human_bones


def first(*names):
    for n in names:
        b = J.get(n)
        if b and b in arm.data.bones:
            return b
    return None


def pick_toe(side):
    ex, tip = first(side + "足先EX"), first(side + "つま先")
    return ex or tip


mapping = {
    "hips": HIPS or first("下半身", "センター"),
    "spine": first("上半身"),
    "chest": first("上半身2"),
    "upper_chest": first("上半身3") if first("上半身2") else None,
    "neck": first("首"),
    "head": first("頭"),
    "left_eye": first("左目"), "right_eye": first("右目"),
}
for side, pre in (("左", "left"), ("右", "right")):
    mapping.update({
        f"{pre}_shoulder": first(side + "肩"),
        f"{pre}_upper_arm": first(side + "腕"),
        f"{pre}_lower_arm": first(side + "ひじ"),
        f"{pre}_hand": first(side + "手首"),
        f"{pre}_upper_leg": first(side + "足", side + "足D"),
        f"{pre}_lower_leg": first(side + "ひざ", side + "ひざD"),
        f"{pre}_foot": first(side + "足首", side + "足首D"),
        f"{pre}_toes": pick_toe(side),
    })
    if first(side + "親指０"):
        th = [side + "親指０", side + "親指１", side + "親指２"]
    else:
        th = [side + "親指１", side + "親指２", None]
    for part, n in zip(("metacarpal", "proximal", "distal"), th):
        mapping[f"{pre}_thumb_{part}"] = first(n) if n else None
    for fj, fe in (("人指", "index"), ("中指", "middle"), ("薬指", "ring"), ("小指", "little")):
        for i, part in ((1, "proximal"), (2, "intermediate"), (3, "distal")):
            mapping[f"{pre}_{fe}_{part}"] = first(f"{side}{fj}{'１２３'[i-1]}", f"{side}{fj}{i}")

missing_req = [k for k in ("hips", "spine", "head", "left_upper_arm", "left_lower_arm", "left_hand",
                           "right_upper_arm", "right_lower_arm", "right_hand", "left_upper_leg",
                           "left_lower_leg", "left_foot", "right_upper_leg", "right_lower_leg", "right_foot")
               if not mapping.get(k)]
if missing_req:
    log("WARNING: required humanoid bones not found by MMD names:", missing_req, "- trying add-on auto assignment")
    bpy.ops.vrm.assign_vrm1_humanoid_human_bones_automatically(armature_object_name=arm.name)
for k, v in mapping.items():
    if v:
        getattr(hb, k).node.bone_name = v
humanoid_bones = {v for v in mapping.values() if v}
try:
    humanoid_ok = hb.bones_are_correctly_assigned()
except Exception:
    humanoid_ok = not missing_req
log("humanoid:", {k: v for k, v in mapping.items() if v})

# look-at: bone type, offset = eye midpoint relative to head
le, re_, hd = mapping.get("left_eye"), mapping.get("right_eye"), mapping.get("head")
if le and re_ and hd:
    b = arm.data.bones
    eye_mid = (b[le].head_local + b[re_].head_local) / 2
    ext.vrm1.look_at.type = "bone"
    ext.vrm1.look_at.offset_from_head_bone = tuple(eye_mid - b[hd].head_local)

# ----------------------------------------------------------------------------- 4 spring bones
bpy.context.view_layer.update()


def rigid_shape(o):
    """World-space shape of an MMD rigid body -> ('sphere', c, None, r) / ('capsule', a, b, r)."""
    loc, rot, sc = o.matrix_world.decompose()
    bb = [Vector(c) for c in o.bound_box]
    lo = Vector((min(c.x for c in bb), min(c.y for c in bb), min(c.z for c in bb)))
    hi = Vector((max(c.x for c in bb), max(c.y for c in bb), max(c.z for c in bb)))
    half = [abs((hi[i] - lo[i]) / 2 * sc[i]) for i in range(3)]
    centre = o.matrix_world @ ((lo + hi) / 2)
    kind = str(getattr(o.mmd_rigid, "shape", "")).upper()
    if max(half) < 1e-5:
        return None
    if kind == "SPHERE":
        return ("sphere", centre, None, min(half))
    ax = max(range(3), key=lambda i: half[i])
    others = [half[i] for i in range(3) if i != ax]
    r = sum(others) / 2 if kind == "CAPSULE" else min(others)
    seg = max(half[ax] - r, 0.0)
    if seg < 1e-4:
        return ("sphere", centre, None, r)
    axis = rot @ Vector([1.0 if i == ax else 0.0 for i in range(3)])
    return ("capsule", centre - axis * seg, centre + axis * seg, r)


def seg_dist(p, shp):
    kind, a, b, r = shp
    if kind == "sphere":
        return (p - a).length - r
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.dot(ab), 1e-12)))
    return (p - (a + ab * t)).length - r


def rb_filter(o):
    m = getattr(o.mmd_rigid, "collision_group_mask", None)
    return int(getattr(o.mmd_rigid, "collision_group_number", 0)), (list(m) if m is not None else [False] * 16)


# Static (bone-following) bodies become our colliders. The add-on's own MMD collider import is
# unusable (copies the body's scale into the radius, bone-local ends, one body per bone, ignores
# MMD collision masks), so hide the static bodies from it and build colliders ourselves below.
statics = []   # (obj, bone, shape, group, mask)
dyn_by_bone = {}
for o in rigid_objs:
    bn = rb_bone(o)
    if not bn:
        continue
    t = str(getattr(o.mmd_rigid, "type", ""))
    grp, mask = rb_filter(o)
    if t == "0":
        shp = rigid_shape(o)
        if shp:
            statics.append((o, bn, shp, grp, mask))
    elif t in ("1", "2"):
        sz = list(getattr(o.mmd_rigid, "size", (0.02, 0, 0)))
        rad = min(x for x in sz if x > 0) if str(o.mmd_rigid.shape).upper() == "BOX" else sz[0]
        dyn_by_bone.setdefault(bn, []).append((grp, mask, rad))
if not args.no_spring and rigid_objs:
    for o, *_ in statics:
        o.constraints["mmd_tools_rigid_parent"].subtarget = ""
    try:
        bpy.ops.vrm.assign_spring_bone1_from_mmd(armature_object_name=arm.name)
    except Exception:
        log("spring bone assignment failed:\n" + traceback.format_exc())
    sb = ext.spring_bone1
    bones = arm.data.bones
    # drop joints that sit on humanoid bones; extend chains with their tip bone so the last one swings
    for i in reversed(range(len(sb.springs))):
        sp = sb.springs[i]
        for k in reversed(range(len(sp.joints))):
            if sp.joints[k].node.bone_name in humanoid_bones:
                sp.joints.remove(k)
        if len(sp.joints):
            last = bones.get(sp.joints[-1].node.bone_name)
            used = {j.node.bone_name for j in sp.joints}
            if last and last.children:
                tip = max(last.children, key=lambda c: len(list(c.children_recursive)))
                if tip.name not in used and tip.name not in humanoid_bones:
                    sp.add_joint().node.bone_name = tip.name
        if len(sp.joints) < 2:
            sb.springs.remove(i)
            continue
        hr = 0.012
        for jt in sp.joints:
            jt.stiffness = 0.8
            jt.drag_force = 0.4
            jt.gravity_power = 0.05
            # hit radius from the MMD body on that bone (tip joints inherit the previous one)
            radii = [d[2] for d in dyn_by_bone.get(jt.node.bone_name, []) if d[2] > 0]
            if radii:
                hr = max(0.004, min(0.02, min(radii) * 0.8))
            jt.hit_radius = hr
    # One VRM collider (+ group) per static rigid body, placed through the add-on's own setters.
    # Only bodies that are real body parts become colliders: on a humanoid bone (no fingers), not
    # oversized and not floating away from their bone. MMD models also carry helper "blocker"
    # bodies (e.g. a 30 cm ball in front of the chest that keeps side hair from falling forward,
    # or balls above the skull for the ears); they rely on MMD joint angle limits, which VRM
    # springs don't have, so in VRM they just shove hair outward.
    arm_mw = arm.matrix_world
    bone_role = {v: k for k, v in mapping.items() if v}
    for jn, role_ in (("下半身", "hips"), ("腰", "hips"), ("上半身3", "upper_chest")):
        if J.get(jn) and J[jn] not in bone_role:
            bone_role[J[jn]] = role_  # deform bones that sit under the new Hips / extra spine
    made = []   # (group_uuid, shape, group, mask, role)
    helper_skipped = []
    for o, bn, shp, grp, mask in statics:
        pb = arm.pose.bones.get(bn)
        if pb is None or bn in dyn_by_bone:
            continue
        role = bone_role.get(bn, "")
        b = bones[bn]
        bh, bt = arm_mw @ b.head_local, arm_mw @ b.tail_local
        centre = shp[1] if shp[0] == "sphere" else (shp[1] + shp[2]) / 2
        off = seg_dist(centre, ("capsule", bh, bt, 0.0)) if (bt - bh).length > 1e-6 else (centre - bh).length
        if (not role or any(f in role for f in ("thumb", "index", "middle", "ring", "little"))
                or shp[3] > 0.15 or off > shp[3] + 0.08):
            helper_skipped.append(o.mmd_rigid.name_j or o.name)
            continue
        col = sb.add_collider(bpy.context, arm)
        col.node.bone_name = bn
        to_bone = (arm_mw @ pb.matrix).inverted()
        kind, a, b, r = shp
        if kind == "sphere":
            col.shape_type = col.SHAPE_TYPE_SPHERE.identifier
            col.shape.sphere.radius = r
            col.shape.sphere.offset = tuple(to_bone @ a)
        else:
            col.shape_type = col.SHAPE_TYPE_CAPSULE.identifier
            col.shape.capsule.radius = r
            col.shape.capsule.offset = tuple(to_bone @ a)
            col.shape.capsule.tail = tuple(to_bone @ b)
        cg = sb.add_collider_group()
        cg.vrm_name = f"{bn}-mmd-{len(made)}"
        cg.add_collider().collider_uuid = col.uuid
        made.append((cg.uuid, shp, grp, mask, role))
    bpy.context.view_layer.update()

    # Link each chain to the bodies it collides with in MMD (both collision masks must allow it),
    # that are within its reach, and that don't already touch it at rest (those would shove the
    # chain out permanently, e.g. a chest body around breast bones).
    links = skipped_mask = skipped_rest = 0
    for sp in sb.springs:
        jb = [bones.get(j.node.bone_name) for j in sp.joints]
        jb = [b for b in jb if b]
        if not jb:
            continue
        filt = [f for b in jb for f in dyn_by_bone.get(b.name, [])]
        # chains hanging from the head/neck (hair, ears) don't collide with the arms: lowering the
        # arms would sweep them through long hair and lift it
        anc, on_head = jb[0].parent, False
        while anc is not None:
            if bone_role.get(anc.name) in ("head", "neck"):
                on_head = True
                break
            anc = anc.parent
        hr = max(j.hit_radius for j in sp.joints)
        pts = [arm_mw @ b.head_local for b in jb] + [arm_mw @ ((b.head_local + b.tail_local) / 2) for b in jb]
        chain_root = arm_mw @ jb[0].head_local
        reach = sum(b.length for b in jb) + 0.05
        for guuid, shp, grp, mask, role in made:
            if on_head and any(k in role for k in ("shoulder", "arm", "hand")):
                continue
            if filt and not any(not dm[grp] and not mask[dg] for dg, dm, _ in filt):
                skipped_mask += 1
                continue
            if seg_dist(chain_root, shp) > reach:
                continue
            if any(seg_dist(p, shp) < hr + 0.005 for p in pts):
                skipped_rest += 1
                continue
            sp.add_collider_group().collider_group_uuid = guuid
            links += 1
    if helper_skipped:
        log(f"skipped {len(helper_skipped)} helper/accessory rigid bodies as colliders: {helper_skipped[:12]}"
            + (" ..." if len(helper_skipped) > 12 else ""))
    log(f"spring bones: {len(sb.springs)} chains, {len(made)} colliders from static rigid bodies, "
        f"{links} chain-collider links ({skipped_mask} excluded by MMD collision masks, "
        f"{skipped_rest} touching the chain at rest)")

# ----------------------------------------------------------------------------- 5 drop physics / root
for o in [o for o in bpy.data.objects if getattr(o, "mmd_type", "") in
          ("RIGID_BODY", "JOINT", "RIGID_GRP_OBJ", "JOINT_GRP_OBJ", "TEMPORARY_GRP_OBJ")]:
    bpy.data.objects.remove(o, do_unlink=True)
if bpy.context.scene.rigidbody_world:
    try:
        bpy.ops.rigidbody.world_remove()
    except Exception:
        pass
if arm.parent:
    mw = arm.matrix_world.copy()
    arm.parent = None
    arm.matrix_world = mw
if root:
    bpy.data.objects.remove(root, do_unlink=True)

# ----------------------------------------------------------------------------- 6 materials -> MToon
import numpy as np

HINT_BLEND = re.compile(r"(lens|レンズ|glass|ガラス|涙|tear|頬|blush|cheek|影|shadow|ハイライト|highlight|透明|alpha|effect)", re.I)
alpha_cache = {}


def texture_alpha_kind(img):
    """OPAQUE / MASK / BLEND guess from the texture's alpha channel."""
    if img is None:
        return "OPAQUE"
    if img.name in alpha_cache:
        return alpha_cache[img.name]
    kind = "OPAQUE"
    try:
        if img.channels == 4 and img.size[0] > 0:
            px = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
            img.pixels.foreach_get(px)
            a = px[3::4]
            trans = np.count_nonzero(a < 0.95)
            if trans > a.size * 0.001:
                semi = np.count_nonzero((a > 0.05) & (a < 0.95))
                kind = "BLEND" if semi > trans * 0.6 else "MASK"
    except Exception as e:
        log("alpha probe failed for", img.name, e)
    alpha_cache[img.name] = kind
    return kind


def toon_shade(img):
    """MMD toon ramp: bottom of the image is the shadow colour."""
    if img is None or img.size[0] == 0:
        return None
    try:
        w, h = img.size
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape(h, w, 4)
        c = px[: max(1, h // 8), :, :3].reshape(-1, 3).mean(axis=0)
        return tuple(float(max(0.45, min(1.0, x))) for x in c)
    except Exception:
        return None


missing_tex = set()


def node_img(mat, name):
    n = mat.node_tree.nodes.get(name) if mat.node_tree else None
    img = getattr(n, "image", None) if n else None
    if img is None:
        return None
    if img.packed_file or os.path.exists(bpy.path.abspath(img.filepath_raw or img.filepath)):
        return img
    missing_tex.add(os.path.basename(img.filepath_raw or img.filepath or img.name))
    return None  # a missing file would make the VRM exporter cancel


hidden_mats = set()
done = set()
for m in meshes:
    for slot in m.material_slots:
        mat = slot.material
        if not mat or mat.name in done:
            continue
        done.add(mat.name)
        mm = mat.mmd_material
        alpha = float(mm.alpha)
        if alpha <= 0.001:
            hidden_mats.add(mat.name)
        tex = node_img(mat, "mmd_base_tex")
        toon = node_img(mat, "mmd_toon_tex")
        sph = node_img(mat, "mmd_sphere_tex")
        diffuse = tuple(mm.diffuse_color)[:3]
        double_sided = bool(mm.is_double_sided)
        edge_on = bool(getattr(mm, "enabled_toon_edge", False))
        edge_col = tuple(getattr(mm, "edge_color", (0, 0, 0, 1)))
        edge_w = float(getattr(mm, "edge_weight", 1.0))
        sph_mode = str(getattr(mm, "sphere_texture_type", ""))

        for img in (tex, sph):
            if img is not None and img.colorspace_settings.name != "sRGB":
                try:
                    img.colorspace_settings.name = "sRGB"
                except Exception:
                    pass
        g = mat.vrm_addon_extension.mtoon1
        g.enabled = True  # converts the MMD node tree to MToon 1.0 and flags the material
        mt = g.extensions.vrmc_materials_mtoon
        if tex:
            g.pbr_metallic_roughness.base_color_texture.index.source = tex
            mt.shade_multiply_texture.index.source = tex
            g.pbr_metallic_roughness.base_color_factor = (1.0, 1.0, 1.0, alpha)
        else:  # no texture, or its file is missing: the add-on may have linked it anyway -> clear
            g.pbr_metallic_roughness.base_color_texture.index.source = None
            mt.shade_multiply_texture.index.source = None
            g.pbr_metallic_roughness.base_color_factor = (*diffuse, alpha)
        shade = toon_shade(toon) or (0.80, 0.78, 0.82)
        mt.shade_color_factor = shade if tex else tuple(d * s for d, s in zip(diffuse, shade))
        mt.shading_toony_factor = 0.95
        mt.shading_shift_factor = -0.05
        if sph is not None and sph_mode in ("2", "ADD", "SPHERE_ADD"):
            mt.matcap_texture.index.source = sph
            mt.matcap_factor = (1.0, 1.0, 1.0)
        g.double_sided = double_sided

        kind = texture_alpha_kind(tex)
        if alpha < 0.999 or (kind != "OPAQUE" and HINT_BLEND.search(mat.name)):
            kind = "BLEND"
        g.alpha_mode = kind
        if kind == "MASK":
            g.alpha_cutoff = 0.5
        if not args.no_outline and edge_on and edge_w > 0 and kind != "BLEND":
            mt.outline_width_mode = "worldCoordinates"
            mt.outline_width_factor = min(0.004, 0.0012 * edge_w * args.outline_scale)
            mt.outline_color_factor = edge_col[:3]
            mt.outline_lighting_mix_factor = 1.0
log(f"materials: {len(done)} converted to MToon, {len(hidden_mats)} invisible")
if missing_tex:
    log(f"WARNING: {len(missing_tex)} texture file(s) missing next to the PMX, using flat colours: "
        f"{sorted(missing_tex)}")

if hidden_mats and not args.keep_hidden:
    for m in meshes:
        idxs = [i for i, s in enumerate(m.material_slots) if s.material and s.material.name in hidden_mats]
        if not idxs:
            continue
        set_active(m, "EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")
        for i in idxs:
            m.active_material_index = i
            bpy.ops.object.material_slot_select()
        bpy.ops.mesh.delete(type="FACE")
        bpy.ops.object.mode_set(mode="OBJECT")
        set_active(m)
        bpy.ops.object.material_slot_remove_unused()
    log(f"removed geometry of invisible materials: {sorted(hidden_mats)}")

# ----------------------------------------------------------------------------- 7 expressions
try:
    bpy.ops.vrm.assign_vrm1_expressions_from_mmd(armature_object_name=arm.name)
except Exception:
    log("expression auto-assign failed:\n" + traceback.format_exc())
preset = ext.vrm1.expressions.preset
keys = {}
for m in meshes:
    if m.data.shape_keys:
        for kb in m.data.shape_keys.key_blocks[1:]:
            keys.setdefault(kb.name, []).append(m.name)
FALLBACK = {
    "aa": ["あ", "あ２", "a", "A"], "ih": ["い", "i", "I"], "ou": ["う", "u", "U"],
    "ee": ["え", "e", "E"], "oh": ["お", "o", "O"],
    "blink": ["まばたき", "瞬き", "blink", "Blink"],
    "blink_left": ["ウィンク", "ウィンク２", "ｳｨﾝｸ", "wink", "Wink"],
    "blink_right": ["ウィンク右", "ウィンク２右", "ｳｨﾝｸ右", "wink_R", "Wink right"],
    "happy": ["笑い", "にこり", "にっこり", "笑顔", "smile", "Smile"],
    "angry": ["怒り", "真面目", "angry", "Angry"],
    "sad": ["困る", "悲しい", "困り", "sad", "Sad"],
    "relaxed": ["なごみ", "穏やか", "relaxed"],
    "surprised": ["びっくり", "驚き", "surprised", "Surprised"],
}
used_keys = set()
for ename, cands in FALLBACK.items():
    ex = getattr(preset, ename)
    if not len(ex.morph_target_binds):
        for c in cands:
            if c in keys:
                for mname in keys[c]:
                    bnd = ex.morph_target_binds.add()
                    bnd.node.mesh_object_name = mname
                    bnd.index = c
                    bnd.weight = 1.0
                break
    for bnd in ex.morph_target_binds:
        used_keys.add(bnd.index)
# lookUp/lookDown from MMD '上'/'下' are eyebrow morphs, not gaze - VRM uses the eye bones instead
for ename in ("look_up", "look_down"):
    ex = getattr(preset, ename)
    while len(ex.morph_target_binds):
        ex.morph_target_binds.remove(0)
for ename in ("blink", "blink_left", "blink_right"):
    getattr(preset, ename).is_binary = False
for ename in ("happy", "angry", "sad", "relaxed", "surprised"):
    ex = getattr(preset, ename)
    if len(ex.morph_target_binds):
        ex.override_blink = "blend"
set_expr = {e: bool(len(getattr(preset, e).morph_target_binds)) for e in FALLBACK}
log("preset expressions:", {e: [b.index for b in getattr(preset, e).morph_target_binds] for e in FALLBACK if set_expr[e]})

if not args.no_custom_expr:
    custom = ext.vrm1.expressions.custom
    taken = {c.custom_name for c in custom}
    reserved = {"happy", "angry", "sad", "relaxed", "surprised", "neutral", "aa", "ih", "ou", "ee", "oh",
                "blink", "blinkLeft", "blinkRight", "lookUp", "lookDown", "lookLeft", "lookRight"}
    n_custom = 0
    for kname, mnames in keys.items():
        if kname in used_keys or kname in taken or kname in reserved:
            continue
        ce = custom.add()
        ce.custom_name = kname
        for mname in mnames:
            bnd = ce.morph_target_binds.add()
            bnd.node.mesh_object_name = mname
            bnd.index = kname
            bnd.weight = 1.0
        taken.add(kname)
        n_custom += 1
    log(f"custom expressions: {n_custom}")
try:
    bpy.ops.vrm.update_vrm1_expression_ui_list_elements()
except Exception:
    pass

# ----------------------------------------------------------------------------- 8 meta / first person
meta = ext.vrm1.meta
meta.vrm_name = model_name
author = args.author
if not author and model_comment:
    mt_ = re.search(r"(?:モデル|model(?:ing)?|作者|author|制作)\s*[:：]\s*([^\r\n]+)", model_comment, re.I)
    author = mt_.group(1).strip() if mt_ else ""
if not len(meta.authors):
    meta.authors.add()
meta.authors[0].value = author or "Unknown"
if model_comment:
    meta.copyright_information = model_comment.strip()[:1000]
meta.version = "1.0"
fp = ext.vrm1.first_person
if not len(fp.mesh_annotations):
    for m in meshes:
        a = fp.mesh_annotations.add()
        a.node.mesh_object_name = m.name
        a.type = "auto"
ext.vrm1.humanoid.pose = "autoPose"
if le and re_ and hd:
    ext.vrm1.look_at.type = "bone"

# ----------------------------------------------------------------------------- 9 validate + save + export
def widen_joint_indices(path):
    """Rewrite 8-bit JOINTS_n accessors as 16-bit.

    The glTF exporter stores a primitive's joint indices as bytes when it uses < 256 joints.
    three-vrm's VRMUtils.combineSkeletons remaps those indices in place without widening, so on
    rigs with more than 256 bones (normal for MMD) they wrap and vertices jump to unrelated bones
    (hair following the forearms). 16-bit indices make the file safe for such loaders.
    """
    import struct
    data = open(path, "rb").read()
    magic, ver, _ = struct.unpack_from("<4sII", data, 0)
    off, chunks = 12, []
    while off < len(data):
        ln, typ = struct.unpack_from("<I4s", data, off)
        chunks.append([typ, bytearray(data[off + 8: off + 8 + ln])])
        off += 8 + ln
    js = json.loads(bytes(chunks[0][1]).decode("utf-8"))
    binc = next(c for c in chunks if c[0] == b"BIN\x00")
    acc, bvs = js["accessors"], js["bufferViews"]
    todo = sorted({a for m in js.get("meshes", []) for pr in m["primitives"]
                   for k, a in pr["attributes"].items()
                   if k.startswith("JOINTS_") and acc[a]["componentType"] == 5121 and "bufferView" in acc[a]})
    for ai in todo:
        A = acc[ai]
        bv = bvs[A["bufferView"]]
        comps = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[A["type"]]
        stride = bv.get("byteStride", comps)
        base = bv.get("byteOffset", 0) + A.get("byteOffset", 0)
        vals = []
        for i in range(A["count"]):
            p0 = base + i * stride
            vals.extend(binc[1][p0: p0 + comps])
        while len(binc[1]) % 4:
            binc[1].append(0)
        new_off = len(binc[1])
        binc[1].extend(struct.pack(f"<{len(vals)}H", *vals))
        bvs.append({"buffer": 0, "byteOffset": new_off, "byteLength": len(vals) * 2, "target": 34962})
        A["bufferView"] = len(bvs) - 1
        A.pop("byteOffset", None)
        A["componentType"] = 5123
    if not todo:
        return 0
    while len(binc[1]) % 4:
        binc[1].append(0)
    js["buffers"][0]["byteLength"] = len(binc[1])
    jb = json.dumps(js, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    jb += b" " * (-len(jb) % 4)
    chunks[0][1] = bytearray(jb)
    body = b"".join(struct.pack("<I4s", len(c[1]), c[0]) + bytes(c[1]) for c in chunks)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", magic, ver, 12 + len(body)) + body)
    return len(todo)


class _Err:
    def __init__(self):
        self.items = []
    def clear(self):
        self.items.clear()
    def add(self):
        from types import SimpleNamespace
        e = SimpleNamespace(name="", severity=0, message="")
        self.items.append(e)
        return e
    def __len__(self):
        return len(self.items)


errs = _Err()
try:
    validation = sys.modules[VRM + ".editor.validation"]
    validation.WM_OT_vrm_validator.detect_errors(bpy.context, errs, arm.name)
    sev = {0: "ERROR", 1: "WARN", 2: "WARN", 3: "INFO"}
    for e in errs.items:
        log(f"validate {sev.get(e.severity, e.severity)}: {e.message}")
except Exception:
    log("validator not callable:\n" + traceback.format_exc())
fatal = [e for e in errs.items if e.severity == 0]

set_active(arm)
if not args.no_blend:
    blend_path = os.path.splitext(out)[0] + ".blend"
    bpy.ops.wm.save_as_mainfile(filepath=blend_path, compress=True)
    if os.path.exists(blend_path + "1"):  # Blender's backup of the previous run
        os.remove(blend_path + "1")
    log("saved", blend_path)

status = "prepared"
if not humanoid_ok:
    log("ERROR: this model has no usable humanoid skeleton (accessory/prop PMX?) - not exported. "
        "Assign bones manually in the saved .blend (VRM panel -> Humanoid) and export from there.")
    status = "not humanoid"
elif not args.no_export:
    if fatal:
        log("ERRORS present - attempting export anyway")
    res = bpy.ops.export_scene.vrm(filepath=out, armature_object_name=arm.name, ignore_warning=True,
                                   export_invisibles=False, export_only_selections=False,
                                   enable_advanced_preferences=True, export_all_influences=False,
                                   export_lights=False, export_gltf_animations=False,
                                   export_try_sparse_sk=not args.dense_shapekeys)
    status = "exported" if "FINISHED" in res and os.path.exists(out) else f"export failed {res}"
    if status == "exported":
        try:
            n = widen_joint_indices(out)
            if n:
                log(f"widened {n} 8-bit JOINTS_0 accessor(s) to 16 bit")
        except Exception:
            log("could not post-process joint indices:\n" + traceback.format_exc())
    if os.path.exists(out):
        log(f"wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB)")
log("status:", status)

with open(os.path.splitext(out)[0] + ".log.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(log_lines) + "\n")
sys.exit(0 if status in ("exported", "prepared") else 1)
