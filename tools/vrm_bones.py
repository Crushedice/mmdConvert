# Print world-space rest positions of VRM humanoid bones (glTF coords: +Y up, VRM1 faces +Z)
import json, struct, sys
import numpy as np

data = open(sys.argv[1], "rb").read()
off, chunks = 12, {}
while off < len(data):
    l, t = struct.unpack_from("<I4s", data, off)
    chunks[t] = data[off + 8: off + 8 + l]
    off += 8 + l
g = json.loads(chunks[b"JSON"])
nodes = g["nodes"]
parent = {}
for i, n in enumerate(nodes):
    for c in n.get("children", []):
        parent[c] = i


def local(n):
    if "matrix" in n:
        return np.array(n["matrix"]).reshape(4, 4).T
    t = n.get("translation", [0, 0, 0]); r = n.get("rotation", [0, 0, 0, 1]); s = n.get("scale", [1, 1, 1])
    x, y, z, w = r
    R = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                  [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                  [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
    M = np.eye(4); M[:3, :3] = R * np.array(s); M[:3, 3] = t
    return M


def world(i):
    M = local(nodes[i])
    while i in parent:
        i = parent[i]
        M = local(nodes[i]) @ M
    return M


hb = g["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]
names = sys.argv[2:] or ["hips", "spine", "chest", "neck", "head", "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
                         "leftMiddleProximal", "rightUpperArm", "rightLowerArm", "rightHand", "leftUpperLeg", "leftLowerLeg", "leftFoot", "leftToes"]
for k in names:
    if k in hb:
        i = hb[k]["node"]
        p = world(i)[:3, 3]
        rot = nodes[i].get("rotation")
        print(f"{k:20s} {nodes[i]['name']:14s} pos=({p[0]:+.3f},{p[1]:+.3f},{p[2]:+.3f}) localrot={'none' if not rot or np.allclose(rot,[0,0,0,1],atol=1e-4) else [round(x,3) for x in rot]}")
