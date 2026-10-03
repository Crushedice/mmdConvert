# Quick structural check of a .vrm (glb) file. Runs with any Python 3:
#   python vrm_inspect.py model.vrm
import json, struct, sys, collections

path = sys.argv[1]
with open(path, "rb") as f:
    data = f.read()
magic, ver, length = struct.unpack_from("<4sII", data, 0)
assert magic == b"glTF", "not a glb"
off = 12
chunks = {}
while off < len(data):
    clen, ctype = struct.unpack_from("<I4s", data, off)
    chunks[ctype] = data[off + 8: off + 8 + clen]
    off += 8 + clen
g = json.loads(chunks[b"JSON"])
bin_size = len(chunks.get(b"BIN\x00", b""))
print(f"file {len(data)/1e6:.1f} MB, BIN {bin_size/1e6:.1f} MB, glTF {g['asset'].get('version')} gen={g['asset'].get('generator')}")
print("extensionsUsed:", g.get("extensionsUsed"))

bv = g.get("bufferViews", [])
acc = g.get("accessors", [])
usage = collections.Counter()
img_bytes = 0
for im in g.get("images", []):
    if "bufferView" in im:
        img_bytes += bv[im["bufferView"]]["byteLength"]
usage["images"] = img_bytes
sparse = 0
for mesh in g.get("meshes", []):
    for p in mesh["primitives"]:
        for k, a in p["attributes"].items():
            usage["attr:" + k] += bv[acc[a]["bufferView"]]["byteLength"] if "bufferView" in acc[a] else 0
        if "indices" in p:
            usage["indices"] += bv[acc[p["indices"]]["bufferView"]]["byteLength"]
        for t in p.get("targets", []):
            for k, a in t.items():
                A = acc[a]
                if "sparse" in A:
                    sparse += 1
                    s = A["sparse"]
                    usage["morph(sparse):" + k] += bv[s["indices"]["bufferView"]]["byteLength"] + bv[s["values"]["bufferView"]]["byteLength"]
                elif "bufferView" in A:
                    usage["morph:" + k] += bv[A["bufferView"]]["byteLength"]
for k, v in usage.most_common():
    print(f"  {k:24s} {v/1e6:8.2f} MB")
print(f"meshes {len(g.get('meshes', []))}, primitives {sum(len(m['primitives']) for m in g.get('meshes', []))}, "
      f"morph targets {max((len(p.get('targets', [])) for m in g.get('meshes', []) for p in m['primitives']), default=0)}, sparse accessors {sparse}")
print(f"nodes {len(g.get('nodes', []))}, skins {len(g.get('skins', []))}, joints {sum(len(s['joints']) for s in g.get('skins', []))}")
print(f"materials {len(g.get('materials', []))}, textures {len(g.get('textures', []))}, images {len(g.get('images', []))}")

ext = g.get("extensions", {})
v1 = ext.get("VRMC_vrm")
v0 = ext.get("VRM")
if v1:
    print("VRMC_vrm specVersion", v1.get("specVersion"))
    hb = v1["humanoid"]["humanBones"]
    print(f"  humanBones {len(hb)}:", ", ".join(f"{k}={g['nodes'][v['node']]['name']}" for k, v in list(hb.items())[:12]), "...")
    ex = v1.get("expressions", {})
    print("  preset expressions:", {k: len(v.get("morphTargetBinds", [])) for k, v in ex.get("preset", {}).items() if v.get("morphTargetBinds")})
    print("  custom expressions:", len(ex.get("custom", {})))
    print("  lookAt:", v1.get("lookAt", {}).get("type"), "firstPerson annotations:", len(v1.get("firstPerson", {}).get("meshAnnotations", [])))
    print("  meta:", {k: v1["meta"].get(k) for k in ("name", "authors", "licenseUrl")})
if v0:
    print("VRM 0.x", v0.get("exporterVersion"))
sb = ext.get("VRMC_springBone")
if sb:
    print(f"VRMC_springBone: {len(sb.get('springs', []))} springs, {sum(len(s['joints']) for s in sb.get('springs', []))} joints, "
          f"{len(sb.get('colliders', []))} colliders, {len(sb.get('colliderGroups', []))} groups")
mt = collections.Counter()
for m in g.get("materials", []):
    e = m.get("extensions", {})
    mt["MToon" if "VRMC_materials_mtoon" in e else ("unlit" if "KHR_materials_unlit" in e else "pbr")] += 1
    mt["alpha:" + m.get("alphaMode", "OPAQUE")] += 1
    if e.get("VRMC_materials_mtoon", {}).get("outlineWidthMode", "none") != "none":
        mt["outline"] += 1
print("materials:", dict(mt))
