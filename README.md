# mmdconv — PMX/PMD → VRM 1.0

One-click conversion of MMD models into working VRM 1.0 avatars using Blender 5.2 headless,
**mmd_tools** and the **VRM Add-on for Blender** (both already installed as Blender extensions).

## Use

- **Drag & drop** one or more `.pmx` files (or folders) onto `convert.bat`
- or put models (with their texture folders) into `input\` and double-click `convert.bat`
- `convert_with_preview.bat` does the same and also renders QA PNGs (T-pose front/side + test pose)

Results land in `output\<model>\`:

| file | what |
|---|---|
| `<model>.vrm` | the avatar (VRM 1.0) |
| `<model>.blend` | the prepared scene, open it to tweak things and re-export via *File → Export → VRM* |
| `<model>.log.txt` | what the converter did + VRM validator messages |
| `preview_*.png` | only with the preview variant |

Options can be passed from PowerShell:

```powershell
.\convert.ps1 "D:\models\Foo\foo.pmx" -Preview -Options "--author Crushed --no-outline"
```

| option | effect |
|---|---|
| `--author NAME` / `--name NAME` | VRM meta (default: PMX name, author guessed from the PMX comment) |
| `--no-spring` | skip rigid body → spring bone conversion |
| `--no-outline` | no MToon outlines (MMD edge lines) |
| `--outline-scale 0.5` | thinner/thicker outlines |
| `--keep-hidden` | keep geometry of alpha-0 materials (normally deleted – they're material-morph toggles) |
| `--no-custom-expr` | don't expose the non-preset morphs as custom expressions |
| `--scale 0.08` | PMX units → metres (0.08 = standard) |
| `--dense-shapekeys` | no sparse accessors (bigger file, only for ancient loaders) |

## What the converter does

1. Imports the PMX with mmd_tools (scale 0.08, Japanese bone names kept, L/R suffixes).
2. Strips MMD rig machinery: IK bones & constraints, additional-transform (付与) constraints,
   mmd_tools `_dummy_` / `_shadow_` helper bones, drivers.
3. Merges **D-bones** (`足D ひざD 足首D 腕D ひじD …`) into their base bones — weights and children
   (twist bones etc.) move over, so the mesh follows the humanoid bones.
4. Adds a real **Hips** bone (MMD has 上半身/下半身 as siblings; VRM needs spine under hips).
5. Maps the VRM humanoid from standard MMD names (センター chain → Hips, 上半身/上半身2(/3), 首, 頭, 目,
   肩/腕/ひじ/手首, 足/ひざ/足首/足先EX, all fingers incl. 親指０).
6. Rigid bodies → **VRM spring bones** (dynamic bodies → chains, static bodies → colliders), chains get
   their tip bone appended so the last segment swings; tuned defaults (stiffness 0.8, drag 0.4).
7. Morphs → **expressions**: あいうえお, まばたき, ウィンク(右), 笑い, 怒り, 困る, なごみ, びっくり
   (+ fallbacks), eye look-at via bones; every other morph becomes a custom expression.
8. Materials → **MToon 1.0**: base texture, shade colour sampled from the MMD toon ramp, `.spa` sphere →
   matcap, alpha mode guessed from texture alpha + MMD alpha, MMD edge → outline, double-sided flag.
9. Removes physics objects and invisible (alpha 0) material geometry, fills VRM meta, runs the VRM
   validator, exports with sparse shape keys. The A-pose is converted to T-pose automatically on export.

## Things to check / known limits

- **License**: the VRM meta defaults to "Unknown" author / restrictive defaults. Respect the PMX
  model's terms (readme in the model folder) before distributing a converted VRM.
- Non-standard rigs (no 上半身/下半身/足 names, e.g. some game rips) fall back to the VRM add-on's
  automatic bone assignment and may need fixing in the `.blend`.
- Accessory/prop PMX files without a body skeleton are reported as "not humanoid" and skipped
  (the `.blend` is still saved). Missing texture files are logged and replaced by flat colours.
- Material morphs (MMD toggles like "OFF身体") and bone morphs don't carry over — only vertex morphs.
- Spring bone feel is generic; tweak in the `.blend` (VRM panel → Spring Bone) if hair is too stiff/floppy.
- Outputs are VRM **1.0**. Apps still limited to 0.x (e.g. older VSeeFace builds) need UniVRM's 1.0→0.x
  migration or a separate 0.x export.

## Files

```
convert.bat / convert_with_preview.bat   drag & drop entry points
convert.ps1                               batch driver
config.ps1                                Blender paths
tools\pmx2vrm.py                          the converter (Blender script)
tools\preview_vrm.py                      QA renders of a finished .vrm
tools\vrm_inspect.py / vrm_bones.py       file structure / skeleton dumps (plain Python)
tools\qa_run.ps1                          convert + preview + inspect in one go (dev)
```
