"""
render_setup.py – Produkt-Animation aus einem 3D-Modell, komplett headless mit Blender + Cycles.
Turntable (360°-Drehung) oder Zusammenbau (Teile fliegen zusammen), jeweils als nahtloser Web-Loop.

Aufruf (aus diesem Ordner). Ausgabe landet in renders/<name>/:
renders/turntable/, renders/assembly-file/ (Explosion aus der Datei), renders/assembly-auto/ (berechnet)

  Testbilder (TEST_FRAMES, halbe Auflösung, wenig Samples) -> .../test/
    /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
        --python render_setup.py -- --test
    ... --python render_setup.py -- --test --frames 1,45,90   # andere Frames testen

  Ganze Sequenz in einer Qualitätsstufe aus QUALITY -> .../preview/ bzw. .../demo/ bzw. direkt .../
    ... --python render_setup.py -- --preview    # 960x540, schnell
    ... --python render_setup.py -- --demo       # 1280x720
    ... --python render_setup.py -- --final      # 1920x1080 (+ transparent in .../frames_alpha/)
    ... --python render_setup.py -- --final --resume     # abgebrochenen Lauf fortsetzen

  Nur Szene aufbauen und als .blend speichern (zum Anschauen in der Blender-Oberfläche)
    ... --python render_setup.py -- --blend

  Animation wählen (sonst gelten ANIMATION und EXPLODE_SOURCE):
    ... -- --preview --anim assembly                  # Zusammenbau mit der Explosion aus der Datei
    ... -- --preview --anim assembly --explode auto   # Zusammenbau, Flugbahnen automatisch berechnet

Alle Stellschrauben stehen im Block PARAMETER direkt hierunter.
Abstände von Licht und Hohlkehle sind in "Modellradien" angegeben (Radius der Bounding-Sphere),
damit das Setup auch bei anderen Modellgrößen ohne Umrechnen passt.
"""

import math
import sys
import json
import urllib.request
from pathlib import Path

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

# ════════════════════════════════════════════════════════════════════════════
# PARAMETER
# ════════════════════════════════════════════════════════════════════════════

# ── Pfade (relativ zu diesem Script) ────────────────────────────────────────
MODEL_PATH = "source/Radial_Engine_to_Skfb.fbx"   # .fbx / .obj / .glb / .gltf / .stl / .ply
OUTPUT_DIR = "renders"                            # darunter je Variante ein Ordner, siehe oben
HDRI_DIR = "assets/hdri"

# ── Modell ──────────────────────────────────────────────────────────────────
IMPORT_SCALE = 1.0            # Faktor beim Import. STL/OBJ aus CAD in mm -> 0.001
TARGET_SIZE = None            # größte Kante auf X Meter skalieren; None = echte Größe behalten
MODEL_ROTATION_DEG = (-90, 90, 0)  # Drehung X/Y/Z in Grad. (-90, 90, 0) = Sternmotor steht aufrecht,
                                   # ein Zylinder oben. (0, 0, 0) = wie in der Datei (liegend, Zahnräder oben)
FLOAT_HEIGHT = 0.0            # Abstand Unterkante -> Boden in m (0 = steht auf dem Boden). Beim Zusammenbau
                              # zählt das tiefste Teil im auseinandergeflogenen Zustand, der Motor schwebt dann
NORMALS_MODE = "auto_smooth"  # "auto_smooth" = Normalen neu nach Winkel, "imported" = aus Datei behalten
AUTO_SMOOTH_ANGLE = 30.0      # Grad; Kanten steiler als das bleiben scharf

# ── Materialien ─────────────────────────────────────────────────────────────
# Schlüssel = Teil des Materialnamens aus der Datei. Farben als sRGB-Hex wie im Webdesign.
MATERIALS = {
    "Orange": dict(color="#F2660A", metallic=0.0, roughness=0.5, coat=0.15, coat_roughness=0.25), # seidenmatter Lack
    "Grey":   dict(color="#C4C7CB", metallic=1.0, roughness=0.35),                                  # Aluminium
    "Bolt":   dict(color="#C9CCCF", metallic=1.0, roughness=0.25),                                  # Stahl
    "Glove":  dict(color="#0A8FE0", metallic=1.0, roughness=0.30),                                  # blau eloxiert
}
FALLBACK_MATERIAL = dict(color="#9A9A9A", metallic=0.0, roughness=0.5)

# ── Hintergrund: weiße Hohlkehle ────────────────────────────────────────────
# Die Hohlkehle ist ein Shadow Catcher: Sie wirft Licht zurück und spiegelt sich im Metall, die Kamera
# sieht aber nur ihre Schatten. Gerendert wird mit Alpha (.../frames_alpha/), danach wird jedes Bild
# auf BG_COLOR gelegt (.../frames/). So ist der Hintergrund pixelgenau diese Farbe -> nahtlos auf der Website.
BG_COLOR = "#FFFFFF"          # Hintergrundfarbe im fertigen Bild (= Seitenhintergrund der Website)
BG_BOUNCE = 0.30              # Helligkeit der Hohlkehle für Reflexionen/Streulicht aufs Modell (0–1).
                              # Kleiner = mehr Kontrast und Zeichnung im Metall, wie schwarze Abschatter
SHADOW_OPACITY = 0.75         # Deckkraft der Bodenschatten (0 = keine, 1 = physikalisch voll)
SHADOW_FADE = (0.35, 0.85)    # Schatten weich ausblenden zwischen 35 % und 85 % des Wegs vom Fußpunkt
                              # des Modells zum Bildrand -> Rand ist garantiert exakt BG_COLOR
CYC_WALL_DIST = 5.0           # Abstand Modellmitte -> Rückwand (Modellradien)
CYC_RADIUS = 3.5              # Radius der Rundung Boden -> Wand (Modellradien)
CYC_WIDTH = 30.0              # Breite (Modellradien)
CYC_HEIGHT = 12.0             # Höhe der Rückwand (Modellradien)
CYC_FRONT = 20.0              # wie weit der Boden nach vorne reicht (Modellradien)

# ── Licht ───────────────────────────────────────────────────────────────────
USE_HDRI = True               # Studio-HDRI von Poly Haven, nur für Spiegelungen (Metall, Lack), kein Streulicht
HDRI_NAME = "studio_small_09" # beliebiger Poly-Haven-Name, z. B. "photo_studio_01"
HDRI_RES = "2k"               # 1k / 2k / 4k
HDRI_STRENGTH = 1.0
HDRI_ROTATION_DEG = 0.0

# Area-Lights. azimuth: 0° = von der Kamera aus, +90° = von rechts, 180° = von hinten.
# distance/size in Modellradien, power = Watt bei 1 m Abstand (wird mit Abstand² hochgerechnet).
# floor_shadow: ob das Licht einen Schatten auf die Hohlkehle wirft. Schräge Lichter werfen lange Schatten
# auf Boden und Rückwand -> dort aus, sie beleuchten dann nur das Modell. Den Bodenschatten macht das Top-Licht.
LIGHTS = [
    dict(name="Key",  azimuth=-45, elevation=50, distance=4.0, size=3.0, power=18, color="#FFF6EC", floor_shadow=False),
    dict(name="Fill", azimuth=70,  elevation=25, distance=4.5, size=4.0, power=5,  color="#F2F6FF", floor_shadow=False),
    dict(name="Top",  azimuth=0,   elevation=88, distance=3.5, size=3.5, power=12, color="#FFFFFF", floor_shadow=True),
    dict(name="Rim",  azimuth=160, elevation=30, distance=3.5, size=1.8, power=20, color="#FFFFFF", floor_shadow=False),
    # große, schwache Softbox hinter der Kamera: gibt flachen Metallflächen eine helle Spiegelung
    dict(name="Front", azimuth=0,  elevation=12, distance=7.0, size=6.0, power=3,  color="#FFFFFF", floor_shadow=False),
]

# ── Kamera ──────────────────────────────────────────────────────────────────
CAM_FOCAL_MM = 70.0           # Brennweite; länger = weniger perspektivische Verzerrung
CAM_ELEVATION_DEG = 18.0      # Blick von oben (0 = auf Augenhöhe)
CAM_FRAME_FILL = 0.82         # Anteil des Bildes, den das Modell maximal einnimmt (über die ganze Drehung)
USE_DOF = False               # Tiefenunschärfe
DOF_FSTOP = 8.0

# ── Animation ───────────────────────────────────────────────────────────────
ANIMATION = "turntable"       # "turntable" = nur Drehung, "assembly" = Motor setzt sich zusammen und dreht sich
                              # (auch per Kommandozeile: -- --preview --anim assembly)
FPS = 30
TURNTABLE_START_DEG = 30.0    # Startwinkel (Drehung des Modells in Frame 1)
TURNTABLE_DIRECTION = 1       # 1 = gegen den Uhrzeigersinn (von oben), -1 = im Uhrzeigersinn

# Turntable
FRAMES = 120                  # 120 Frames = 4 s; Frame 121 wäre wieder Frame 1 -> nahtloser Loop

# Zusammenbau: Explosion rückwärts abgespielt und neu getaktet. Woher kommen die Flugbahnen der Teile?
EXPLODE_SOURCE = "file"       # "file" = Explosionsanimation aus der Datei (von Hand gemacht, mechanisch richtig)
                              # "auto" = automatisch berechnet, für Dateien ohne Animation (-- --explode auto)
                              # Hat die Datei keine Animation, wird automatisch "auto" genommen.
SOURCE_ASSEMBLED_FRAME = 16   # nur "file": FBX-Frame, noch zusammengebaut (kurz bevor die Teile losfliegen)
SOURCE_EXPLODED_FRAME = 38    # nur "file": FBX-Frame, ganz auseinander
AUTO_EXPLODE_DISTANCE = 1.0   # nur "auto": Flugweite des äußersten Teils in Modellradien
AUTO_STAGGER = 0.5            # nur "auto": Staffelung; 0 = alle gleichzeitig, 0.9 = stark nacheinander
                              # (außen zuerst los, beim Zusammenbau also zuletzt da)
AUTO_AXIS_RATIO = 1.6         # nur "auto": so viel länger als breit -> Schraube/Bolzen/Welle, fährt längs heraus
AUTO_FLAT_RATIO = 0.5         # nur "auto": so viel dünner als breit -> Scheibe/Platte/Deckel, fährt senkrecht dazu
AUTO_ANCHOR = 0.12            # nur "auto": Teile näher als das an der Mitte (Anteil Modellradius) bleiben stehen
ASSEMBLY_PHASES = [           # Ablauf des Loops in Sekunden; die Summe ist die Videolänge
    ("exploded", 0.6),        # Teile schweben auseinander
    ("assemble", 2.2),        # setzen sich zusammen (weich abgebremst)
    ("assembled", 1.8),       # fertiger Motor dreht sich weiter
    ("explode", 1.0),         # fliegt wieder auseinander ...
    ("exploded", 0.4),        # ... und schließt nahtlos an den Anfang an
]
ASSEMBLY_TURN_DEG = 360       # Drehung über den ganzen Loop (360 = nahtlos, 0 = keine Drehung)
CAM_FOLLOW = True             # Kamera fährt beim Zusammenbauen näher ran; sonst wirkt der fertige Motor klein,
                              # weil das Bild auf die auseinandergeflogenen Teile ausgelegt sein muss

# ── Render ──────────────────────────────────────────────────────────────────
RESOLUTION = (1920, 1080)     # finale Auflösung; die anderen Stufen rechnen davon herunter
QUALITY = {                   # je Modus: Samples, Anteil der Auflösung, Rausch-Schwelle (kleiner = sauberer)
    "test":    dict(samples=48,  scale=0.5,   noise=0.05),   # Einzelbilder zum Look-Check (960x540)
    "preview": dict(samples=48,  scale=0.5,   noise=0.05),   # ganze Sequenz, schnell (~4 s/Frame)
    "demo":    dict(samples=128, scale=2 / 3, noise=0.02),   # ganze Sequenz in 1280x720
    "final":   dict(samples=256, scale=1.0,   noise=0.01),   # volle Qualität (~70 s/Frame auf M1 Pro)
}
TEST_FRAMES = [1, 30, 60]
VIEW_TRANSFORM = "Khronos PBR Neutral"  # farbtreu für Produkte; Alternativen: "AgX", "Standard"
EXPOSURE = 0.0
MOTION_BLUR = False

# ════════════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parent


def log(msg):
    print(f"[render_setup] {msg}", flush=True)


def hex_to_linear(hex_str):
    """sRGB-Hex -> lineare RGB-Werte, wie Blender sie intern erwartet."""
    h = hex_str.lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb)


def direction(azimuth_deg, elevation_deg):
    """Einheitsvektor vom Modell weg. Azimut 0° zeigt zur Kamera (-Y)."""
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    return Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))


def aim(obj, target):
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


# ── 1. Szene & Import ───────────────────────────────────────────────────────

def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_model(path):
    path = ROOT / path
    ext = path.suffix.lower()
    before = set(bpy.data.objects)
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path), global_scale=IMPORT_SCALE)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path), global_scale=IMPORT_SCALE)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=str(path), global_scale=IMPORT_SCALE)
    elif ext == ".ply":
        bpy.ops.wm.ply_import(filepath=str(path), global_scale=IMPORT_SCALE)
    else:
        sys.exit(f"Format {ext} wird nicht unterstützt (STEP vorher z. B. in FreeCAD nach OBJ/GLB wandeln).")
    objs = [o for o in bpy.data.objects if o not in before]
    log(f"importiert: {path.name} -> {len(objs)} Objekte")
    return objs


def set_source_frame(frame):
    """Springt in der importierten Animation auf einen (auch krummen) Frame; Blender schreibt die
    animierten Werte dabei in die Objekte."""
    bpy.context.scene.frame_set(int(frame), subframe=frame - int(frame))


def source_frame(level):
    """Explosionsgrad (0 = zusammengebaut, 1 = auseinander) -> Frame der FBX-Animation."""
    return SOURCE_ASSEMBLED_FRAME + level * (SOURCE_EXPLODED_FRAME - SOURCE_ASSEMBLED_FRAME)


def explosion_curve():
    """Explosionsgrad für jeden Ausgabe-Frame laut ASSEMBLY_PHASES, Übergänge weich (Smoothstep)."""
    hold = {"exploded": 1.0, "assembled": 0.0}
    curve = []
    for phase, seconds in ASSEMBLY_PHASES:
        n = round(seconds * FPS)
        for i in range(n):
            t = i / n
            ease = t * t * (3 - 2 * t)
            curve.append(hold[phase] if phase in hold else 1 - ease if phase == "assemble" else ease)
    return curve


def clear_animation(objs):
    """Friert das Modell im zusammengebauten Zustand ein und entfernt die importierte Animation."""
    set_source_frame(SOURCE_ASSEMBLED_FRAME)
    for o in objs:
        o.animation_data_clear()
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    log(f"importierte Animation entfernt (Zustand aus Frame {SOURCE_ASSEMBLED_FRAME})")


def file_explode(level):
    """Explosionsgrad -> Zustand aus der Explosionsanimation der Datei."""
    set_source_frame(source_frame(level))


def auto_explode(meshes):
    """Berechnet Flugbahnen für Dateien ohne Explosionsanimation. Gibt eine Funktion level -> Zustand zurück.

    Richtung je Teil, aus der Form abgeleitet (Hauptachsen der Punktwolke):
      lang und schlank (Schraube, Bolzen, Welle) -> entlang der Längsachse
      flach (Scheibe, Deckel, Platte)             -> senkrecht zur Fläche
      sonst                                       -> vom Modellzentrum weg
    Äußere Teile fliegen weiter und starten zuerst (kommen beim Zusammenbau also zuletzt an).
    Teile nahe der Mitte bleiben als Kern stehen.
    """
    clouds = {o: world_points([o]) for o in meshes}
    allpts = np.concatenate(list(clouds.values()))
    center = (allpts.min(0) + allpts.max(0)) / 2
    model_r = np.linalg.norm(allpts - center, axis=1).max()

    parts, kinds = [], {"lang": 0, "flach": 0, "radial": 0, "Kern": 0}
    for o, pts in clouds.items():
        r = (pts.min(0) + pts.max(0)) / 2 - center
        dist = np.linalg.norm(r)
        if dist < AUTO_ANCHOR * model_r:
            kinds["Kern"] += 1
            continue
        _, axes = np.linalg.eigh(np.cov((pts - pts.mean(0)).T))          # Spalten: kurze -> lange Achse
        ext = np.ptp((pts - pts.mean(0)) @ axes, axis=0)                  # Ausdehnung entlang der Achsen
        short, mid, long_ = ext
        axis, kind, reach = r / dist, "radial", 1.0
        if long_ > AUTO_AXIS_RATIO * mid:
            axis, kind, reach = axes[:, 2], "lang", 1.3                   # Schrauben etwas weiter raus
        elif short < AUTO_FLAT_RATIO * mid:
            axis, kind = axes[:, 0], "flach"
        if kind != "radial":
            if abs(axis @ r) < 0.2 * dist:                                # Achse quer zur Mitte -> radial
                axis, kind, reach = r / dist, "radial", 1.0
            else:
                axis = axis * np.sign(axis @ r)                           # immer nach außen
        kinds[kind] += 1
        parts.append((o, axis, dist, reach))

    if not parts:
        sys.exit("Automatische Explosion: keine einzelnen Teile gefunden (verschmolzenes Modell?)")
    max_dist = max(p[2] for p in parts)
    order = sorted(range(len(parts)), key=lambda i: -parts[i][2])          # außen zuerst
    rank = {idx: n / max(1, len(parts) - 1) for n, idx in enumerate(order)}
    stagger = min(max(AUTO_STAGGER, 0.0), 0.9)                             # jedes Teil braucht etwas Zeit
    tracks = []
    for i, (o, axis, dist, reach) in enumerate(parts):
        travel = AUTO_EXPLODE_DISTANCE * model_r * (0.35 + 0.65 * dist / max_dist) * reach
        to_parent = (o.matrix_world @ o.matrix_basis.inverted()).to_3x3().inverted()
        delta = to_parent @ Vector(axis * travel)                          # Weltrichtung -> Elternraum
        tracks.append((o, o.location.copy(), delta, stagger * rank[i]))
    log("Automatische Explosion: " + ", ".join(f"{n} {k}" for k, n in kinds.items()))

    def explode(level):
        for o, start, delta, delay in tracks:
            t = min(max((level - delay) / (1 - stagger), 0.0), 1.0)
            o.location = start + delta * (t * t * (3 - 2 * t))
    return explode


def bake_assembly(objs, curve, explode):
    """Schreibt den Zusammenbau als Keyframes: Frame f zeigt den Explosionsgrad curve[f-1].
    Bei "file" bleiben die Bewegungsbahnen die aus der Datei, bei "auto" die berechneten."""
    states = []
    for level in curve:
        explode(level)
        states.append([(o.location.copy(), o.rotation_euler.copy(), o.scale.copy()) for o in objs])
    for o in objs:
        o.animation_data_clear()
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)

    moving = 0
    for i, o in enumerate(objs):
        track = [s[i] for s in states]
        o.location, o.rotation_euler, o.scale = track[0]
        if all(t == track[0] for t in track):
            continue                       # Teil bewegt sich nicht -> keine Keyframes nötig
        moving += 1
        for frame, (loc, rot, scl) in enumerate(track, start=1):
            o.location, o.rotation_euler, o.scale = loc, rot, scl
            for path in ("location", "rotation_euler", "scale"):
                o.keyframe_insert(path, frame=frame)
    log(f"Zusammenbau: {moving} bewegte Teile, {len(curve)} Frames ({len(curve) / FPS:.1f} s Loop)")


# ── 2. Maßstab & Ausrichtung ────────────────────────────────────────────────

def world_points(meshes):
    """Alle Vertices in Weltkoordinaten als numpy-Array (N, 3)."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    chunks = []
    for o in meshes:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        co = np.empty(len(me.vertices) * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        m = np.array(o.matrix_world)
        chunks.append(co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3])
        ev.to_mesh_clear()
    return np.concatenate(chunks)


def build_hierarchy(objs):
    """Hierarchie: Turntable (dreht sich) -> Model (Ausrichtung, Skalierung, Versatz) -> importierte Objekte"""
    turntable = bpy.data.objects.new("Turntable", None)
    model = bpy.data.objects.new("Model", None)
    for o in (turntable, model):
        bpy.context.scene.collection.objects.link(o)
    model.parent = turntable
    for o in objs:
        if o.parent is None:
            o.parent = model
    model.rotation_euler = [math.radians(a) for a in MODEL_ROTATION_DEG]
    return turntable, model


def normalize_model(model, meshes, levels, explode):
    """Zentriert das Modell in XY und stellt es auf den Boden.

    levels: Explosionsgrade, die berücksichtigt werden (Turntable: nur [0]); explode(level) stellt sie ein.
    Zurück kommt je Grad eine Punktwolke in Szenenkoordinaten, daraus werden Kamera und Licht bestimmt.
    """
    def sample():
        clouds = []
        for level in levels:
            explode(level)
            clouds.append(world_points(meshes))
        explode(0.0)
        return clouds

    clouds = sample()
    size = clouds[0].max(0) - clouds[0].min(0)
    log(f"Größe laut Datei: {size[0]:.3f} x {size[1]:.3f} x {size[2]:.3f} m")
    if TARGET_SIZE:
        s = TARGET_SIZE / size.max()
        model.scale = (s, s, s)
        clouds = sample()
        log(f"auf {TARGET_SIZE} m skaliert (Faktor {s:.4f})")

    # Drehachse = Mitte des zusammengebauten Modells; Boden = tiefster Punkt über alle Zustände
    lo, hi = clouds[0].min(0), clouds[0].max(0)
    bottom = min(c[:, 2].min() for c in clouds)
    model.location = (-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, FLOAT_HEIGHT - bottom)
    clouds = sample()
    size = clouds[0].max(0) - clouds[0].min(0)
    log(f"Größe in der Szene: {size[0]:.3f} x {size[1]:.3f} x {size[2]:.3f} m, "
        f"Unterkante {clouds[0][:, 2].min():.3f} m über dem Boden, "
        f"{sum(len(o.data.polygons) for o in meshes):,} Polygone in {len(meshes)} Meshes")
    return clouds


# ── 3. Normalen / Auto Smooth ───────────────────────────────────────────────

def setup_normals(meshes):
    if NORMALS_MODE == "imported":
        log("Normalen: aus der Datei übernommen")
        return
    for o in meshes:
        if o.data.has_custom_normals:
            with bpy.context.temp_override(object=o, active_object=o):
                bpy.ops.mesh.customdata_custom_splitnormals_clear()
    with bpy.context.temp_override(active_object=meshes[0], object=meshes[0],
                                   selected_objects=meshes, selected_editable_objects=meshes):
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(AUTO_SMOOTH_ANGLE), keep_sharp_edges=True)
    log(f"Normalen: Auto Smooth {AUTO_SMOOTH_ANGLE:.0f}°")


# ── 4. Materialien ──────────────────────────────────────────────────────────

def build_principled(mat, spec):
    try:
        mat.use_nodes = True
    except AttributeError:
        pass
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (-300, 0)
    bsdf.inputs["Base Color"].default_value = (*hex_to_linear(spec["color"]), 1.0)
    bsdf.inputs["Metallic"].default_value = spec.get("metallic", 0.0)
    bsdf.inputs["Roughness"].default_value = spec.get("roughness", 0.5)
    bsdf.inputs["Coat Weight"].default_value = spec.get("coat", 0.0)
    bsdf.inputs["Coat Roughness"].default_value = spec.get("coat_roughness", 0.03)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def setup_materials(meshes):
    mats = {slot.material for o in meshes for slot in o.material_slots if slot.material}
    for mat in mats:
        key = next((k for k in MATERIALS if k.lower() in mat.name.lower()), None)
        build_principled(mat, MATERIALS[key] if key else FALLBACK_MATERIAL)
        log(f"Material {mat.name} -> {key or 'Fallback'}")


# ── 5. Hohlkehle ────────────────────────────────────────────────────────────

def build_cyclorama(radius):
    """Boden, der in einer Viertelkreis-Rundung in die Rückwand übergeht (Profil in Y/Z, extrudiert in X)."""
    r = CYC_RADIUS * radius
    wall_y = CYC_WALL_DIST * radius
    half_w = CYC_WIDTH * radius / 2
    profile = [(-CYC_FRONT * radius, 0.0)]
    steps = 24
    for i in range(steps + 1):   # Rundung von unten (-90°) nach hinten oben (0°)
        a = math.radians(-90 + 90 * i / steps)
        profile.append((wall_y - r + r * math.cos(a), r + r * math.sin(a)))
    profile.append((wall_y, CYC_HEIGHT * radius))

    verts = [(x, y, z) for (y, z) in profile for x in (-half_w, half_w)]
    faces = [(2 * i, 2 * i + 1, 2 * i + 3, 2 * i + 2) for i in range(len(profile) - 1)]
    me = bpy.data.meshes.new("Cyclorama")
    me.from_pydata(verts, [], faces)
    me.shade_smooth()
    obj = bpy.data.objects.new("Cyclorama", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.is_shadow_catcher = True   # Kamera sieht nur die Schatten, der Rest wird transparent

    # Matte graue Fläche: bestimmt, wie viel Licht aufs Modell zurückfällt und was das Metall spiegelt
    mat = bpy.data.materials.new("Cyclorama")
    build_principled(mat, dict(color=BG_COLOR, metallic=0.0, roughness=1.0))
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*[BG_BOUNCE * c for c in hex_to_linear(BG_COLOR)], 1.0)
    bsdf.inputs["Specular IOR Level"].default_value = 0.0
    me.materials.append(mat)
    return obj


# ── 6. Licht ────────────────────────────────────────────────────────────────

def download_hdri():
    path = ROOT / HDRI_DIR / f"{HDRI_NAME}_{HDRI_RES}.hdr"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "render_setup.py (Blender product render)"}   # ohne UA antwortet Poly Haven mit 403
    try:
        req = urllib.request.Request(f"https://api.polyhaven.com/files/{HDRI_NAME}", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            url = json.load(r)["hdri"][HDRI_RES]["hdr"]["url"]
        log(f"lade HDRI von Poly Haven: {url}")
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
            path.write_bytes(r.read())
        return path
    except Exception as e:  # ohne Netz geht es auch, dann eben nur mit Area-Lights
        log(f"HDRI-Download fehlgeschlagen ({e}) – weiter ohne HDRI")
        return None


def setup_world():
    world = bpy.data.worlds.new("Studio")
    bpy.context.scene.world = world
    try:
        world.use_nodes = True
    except AttributeError:
        pass
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    hdri = download_hdri() if USE_HDRI else None
    if hdri:
        coord = nt.nodes.new("ShaderNodeTexCoord")
        mapping = nt.nodes.new("ShaderNodeMapping")
        mapping.inputs["Rotation"].default_value[2] = math.radians(HDRI_ROTATION_DEG)
        env = nt.nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(str(hdri))
        nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
        nt.links.new(env.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = HDRI_STRENGTH
        log(f"HDRI: {hdri.name}, Stärke {HDRI_STRENGTH}")
    else:
        bg.inputs["Color"].default_value = (1, 1, 1, 1)
        bg.inputs["Strength"].default_value = 0.2
    # Das HDRI taucht nur in Spiegelungen auf: nicht für die Kamera, kein diffuses Licht
    # (sonst wirft seine hellste Lichtquelle einen großen Schatten auf die Hohlkehle)
    try:
        world.cycles_visibility.camera = False
        world.cycles_visibility.diffuse = False
    except AttributeError:
        pass


def setup_lights(target, radius, meshes):
    # Light Linking: Lichter ohne floor_shadow beleuchten nur diese Collection, nicht die Hohlkehle
    model_only = bpy.data.collections.new("Light Linking – nur Modell")
    for o in meshes:
        model_only.objects.link(o)
    for spec in LIGHTS:
        dist = spec["distance"] * radius
        data = bpy.data.lights.new(spec["name"], type="AREA")
        data.shape = "DISK"
        data.size = spec["size"] * radius
        data.energy = spec["power"] * dist ** 2
        data.color = hex_to_linear(spec.get("color", "#FFFFFF"))
        obj = bpy.data.objects.new(spec["name"], data)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = target + direction(spec["azimuth"], spec["elevation"]) * dist
        aim(obj, target)
        if not spec.get("floor_shadow", True):
            obj.light_linking.receiver_collection = model_only
        log(f"Licht {spec['name']}: {data.energy:.0f} W, Ø {data.size:.2f} m, Abstand {dist:.2f} m")


# ── 7. Kamera ───────────────────────────────────────────────────────────────

def fit_camera(pts, aspect):
    """Findet Abstand und Zielhöhe so, dass das Modell über die GANZE Drehung ins Bild passt
    (höchstens CAM_FRAME_FILL) und vertikal mittig sitzt."""
    tan_half = 18.0 / CAM_FOCAL_MM        # 36-mm-Sensor, Sensor Fit Auto (längere Bildseite)
    tan_x, tan_y = (tan_half, tan_half / aspect) if aspect >= 1 else (tan_half * aspect, tan_half)

    # Punktewolke in 36 Drehstellungen um die Z-Achse
    sub = pts[:: max(1, len(pts) // 20000)]
    rings = []
    for a in np.linspace(0, 2 * math.pi, 36, endpoint=False):
        c, s = math.cos(a), math.sin(a)
        rings.append(np.column_stack((c * sub[:, 0] - s * sub[:, 1], s * sub[:, 0] + c * sub[:, 1], sub[:, 2])))
    cloud = np.concatenate(rings)

    back = np.array(direction(0, CAM_ELEVATION_DEG))      # vom Ziel zur Kamera
    fwd = -back
    right = np.cross(fwd, (0, 0, 1)); right /= np.linalg.norm(right)
    up = np.cross(right, fwd)

    target = np.array([0.0, 0.0, (pts[:, 2].min() + pts[:, 2].max()) / 2])
    radius = np.linalg.norm(pts - target, axis=1).max()
    dist = radius / math.sin(math.atan(tan_y))
    for _ in range(30):
        rel = cloud - (target + back * dist)
        z = rel @ fwd
        nx = np.abs(rel @ right / z / tan_x).max()
        ny = rel @ up / z / tan_y
        # vertikal zentrieren, dann Abstand an gewünschte Füllung anpassen
        target[2] += (ny.max() + ny.min()) / 2 * tan_y * dist / math.cos(math.radians(CAM_ELEVATION_DEG))
        extent = max(nx, (ny.max() - ny.min()) / 2)
        dist *= 1 + (extent / CAM_FRAME_FILL - 1) * 0.7
    return Vector(target), dist, radius


def setup_camera(levels, clouds, curve):
    """Turntable: feste Kamera. Zusammenbau: Kamera je Explosionsgrad eingepasst und pro Frame
    mitgeführt (CAM_FOLLOW) oder fest auf den auseinandergeflogenen Zustand ausgelegt."""
    aspect = RESOLUTION[0] / RESOLUTION[1]
    fits = [fit_camera(c, aspect) for c in clouds]       # (Ziel, Abstand, Radius) je Explosionsgrad
    data = bpy.data.cameras.new("Camera")
    data.lens = CAM_FOCAL_MM
    data.sensor_width = 36
    data.sensor_fit = "AUTO"
    data.clip_start = 0.01
    data.clip_end = 1000
    data.dof.use_dof = USE_DOF
    data.dof.aperture_fstop = DOF_FSTOP
    cam = bpy.data.objects.new("Camera", data)
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam

    def place(target, dist):
        cam.location = target + direction(0, CAM_ELEVATION_DEG) * dist
        aim(cam, target)
        data.dof.focus_distance = dist

    if curve is None or not CAM_FOLLOW:
        target, dist, _ = max(fits, key=lambda f: f[1])  # weitester Ausschnitt, damit alles ins Bild passt
        place(target, dist)
        log(f"Kamera: {CAM_FOCAL_MM:.0f} mm, {dist:.2f} m Abstand, {CAM_ELEVATION_DEG:.0f}° von oben")
    else:
        for frame, level in enumerate(curve, start=1):
            target = Vector([np.interp(level, levels, [f[0][i] for f in fits]) for i in range(3)])
            place(target, float(np.interp(level, levels, [f[1] for f in fits])))
            cam.keyframe_insert("location", frame=frame)
            cam.keyframe_insert("rotation_euler", frame=frame)
            data.keyframe_insert("dof.focus_distance", frame=frame)
        log(f"Kamera: {CAM_FOCAL_MM:.0f} mm, fährt mit von {fits[0][1]:.2f} m (zusammengebaut) "
            f"bis {fits[-1][1]:.2f} m (auseinander), {CAM_ELEVATION_DEG:.0f}° von oben")
    return fits[0][0], fits[0][2]                      # Licht richtet sich nach dem zusammengebauten Modell


# ── 8. Animation ────────────────────────────────────────────────────────────

def animate_turntable(turntable, frames, turn_deg):
    sc = bpy.context.scene
    sc.render.fps = FPS
    sc.render.fps_base = 1
    sc.frame_start, sc.frame_end = 1, frames
    start = math.radians(TURNTABLE_START_DEG)
    end = start + TURNTABLE_DIRECTION * math.radians(turn_deg)
    # Frame frames+1 = volle Umdrehung = identisch mit Frame 1 -> gerendert wird nur bis frames
    for frame, angle in ((1, start), (frames + 1, end)):
        turntable.rotation_euler = (0, 0, angle)
        turntable.keyframe_insert("rotation_euler", index=2, frame=frame)
    log(f"Drehung: {turn_deg:.0f}° in {frames} Frames bei {FPS} fps ({frames / FPS:.1f} s Loop)")


# ── 9. Render-Einstellungen ─────────────────────────────────────────────────

def setup_gpu():
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for backend in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):
        try:
            prefs.compute_device_type = backend
        except TypeError:
            continue
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == backend]
        if gpus:
            for d in prefs.devices:
                d.use = d.type == backend      # nur GPU; CPU dazu bremst auf Apple Silicon eher
            log(f"GPU: {', '.join(d.name for d in gpus)} ({backend})")
            return "GPU"
    log("keine GPU gefunden – rendere auf der CPU")
    return "CPU"


def setup_render(mode):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = setup_gpu()
    q = QUALITY.get(mode, QUALITY["final"])     # --blend speichert mit finalen Einstellungen

    sc.cycles.samples = q["samples"]
    sc.cycles.use_adaptive_sampling = True
    sc.cycles.adaptive_threshold = q["noise"]
    sc.cycles.use_denoising = True
    sc.cycles.denoiser = "OPENIMAGEDENOISE"
    sc.cycles.denoising_input_passes = "RGB_ALBEDO_NORMAL"
    sc.cycles.denoising_prefilter = "ACCURATE"
    sc.cycles.denoising_use_gpu = True
    sc.cycles.use_animated_seed = False        # gleiches Rauschmuster in jedem Frame -> weniger Flimmern
    sc.cycles.max_bounces = 12
    sc.cycles.glossy_bounces = 6
    sc.cycles.caustics_reflective = False
    sc.cycles.caustics_refractive = False
    sc.cycles.blur_glossy = 1.0
    sc.render.use_persistent_data = True       # BVH/Shader zwischen Frames behalten
    sc.render.use_motion_blur = MOTION_BLUR

    sc.render.resolution_x, sc.render.resolution_y = (round(v * q["scale"]) for v in RESOLUTION)
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = True          # Hintergrund transparent, Shadow Catcher liefert Schatten als Alpha
    img = sc.render.image_settings
    img.file_format = "PNG"
    img.color_mode = "RGBA"
    img.color_depth = "8"
    img.compression = 15

    try:
        sc.view_settings.view_transform = VIEW_TRANSFORM
    except TypeError:
        log(f"View Transform '{VIEW_TRANSFORM}' unbekannt – nehme AgX")
        sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "None"
    sc.view_settings.exposure = EXPOSURE
    log(f"Cycles: {sc.cycles.samples} Samples, {sc.render.resolution_x}x{sc.render.resolution_y}, "
        f"Denoising OIDN, {sc.view_settings.view_transform}")


# ── 10. Rendern ─────────────────────────────────────────────────────────────

def shadow_fade_mask(w, h, foot):
    """1 am Fußpunkt des Modells, weich auf 0 zum Bildrand hin (Abstand je Richtung auf den Rand normiert)."""
    cx, cy = foot[0] * w, foot[1] * h
    xs, ys = np.arange(w) + 0.5, np.arange(h) + 0.5          # Blender-Pixel: Zeile 0 = unten
    dx = (xs - cx) / np.where(xs < cx, cx, w - cx)
    dy = (ys - cy) / np.where(ys < cy, cy, h - cy)
    d = np.sqrt(dx[None, :] ** 2 + dy[:, None] ** 2)
    t = np.clip((d - SHADOW_FADE[0]) / (SHADOW_FADE[1] - SHADOW_FADE[0]), 0, 1)
    return (1 - t * t * (3 - 2 * t)).ravel()


def flatten(src, dst, foot):
    """Legt ein RGBA-PNG auf BG_COLOR (nach dem Tonemapping, daher pixelgenau) und speichert es als RGB."""
    img = bpy.data.images.load(str(src))
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(-1, 4)
    bg = np.array([int(BG_COLOR.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)], dtype=np.float32)
    # Reine Schattenpixel (schwarz, teiltransparent) abschwächen und zum Rand ausblenden; Modell bleibt unberührt
    shadow = (px[:, 3] < 0.999) & (px[:, :3].max(axis=1) < 0.02)
    px[shadow, 3] *= SHADOW_OPACITY * shadow_fade_mask(w, h, foot)[shadow]
    alpha = px[:, 3:4]
    px[:, :3] = px[:, :3] * alpha + bg * (1 - alpha)
    px[:, 3] = 1.0
    img.pixels.foreach_set(px.ravel())
    img.filepath_raw = str(dst)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def foot_point(frame):
    """Fußpunkt des Modells auf dem Boden im Bild (0–1); ändert sich, wenn die Kamera mitfährt."""
    sc = bpy.context.scene
    sc.frame_set(frame)
    return world_to_camera_view(sc, sc.camera, Vector((0, 0, 0)))[:2]


def render(mode, resume, frames, name):
    sc = bpy.context.scene
    base = ROOT / OUTPUT_DIR / name
    if mode == "test":
        out = base / "test"
        out.mkdir(parents=True, exist_ok=True)
        for f in frames:
            foot = foot_point(f)
            path = out / f"test_{f:04d}.png"
            sc.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            flatten(path, path, foot)
            log(f"Testbild gespeichert: {path}")
    else:
        if mode != "final":
            base = base / mode        # preview/ bzw. demo/; final direkt in den Animationsordner
        raw = base / "frames_alpha"   # transparent, mit Schatten
        out = base / "frames"         # auf BG_COLOR gelegt -> Quelle fürs Video
        raw.mkdir(parents=True, exist_ok=True)
        out.mkdir(parents=True, exist_ok=True)
        sc.render.filepath = str(raw / "frame_")
        sc.render.use_overwrite = not resume   # --resume überspringt fertige Frames
        sc.render.use_placeholder = resume
        bpy.ops.render.render(animation=True)
        for f in range(sc.frame_start, sc.frame_end + 1):
            name = f"frame_{f:04d}.png"
            if not (resume and (out / name).exists()):
                flatten(raw / name, out / name, foot_point(f))
        log(f"Sequenz fertig: {out} (transparent: {raw})")


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    arg = lambda flag, default: args[args.index(flag) + 1] if flag in args else default
    mode = next((m for m in ("final", "demo", "preview", "blend") if f"--{m}" in args), "test")
    anim = arg("--anim", ANIMATION)
    source = arg("--explode", EXPLODE_SOURCE)
    if anim not in ("turntable", "assembly") or source not in ("file", "auto"):
        sys.exit("Erlaubt: --anim turntable|assembly, --explode file|auto")
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"   # gleichmäßige Bewegung

    reset_scene()
    objs = import_model(MODEL_PATH)
    meshes = [o for o in objs if o.type == "MESH"]
    turntable, model = build_hierarchy(objs)
    if anim == "assembly":
        if source == "file" and not any(o.animation_data and o.animation_data.action for o in objs):
            log("Datei enthält keine Animation -> automatische Explosion")
            source = "auto"
        if source == "auto":
            clear_animation(objs)                 # evtl. vorhandene Animation einfrieren, dann selbst berechnen
            explode = auto_explode(meshes)
        else:
            explode = file_explode
        curve = explosion_curve()
        levels = list(np.linspace(0, 1, 9))       # Stützstellen für Bodenhöhe und Kamerafahrt
        frames, turn, name = len(curve), ASSEMBLY_TURN_DEG, f"assembly-{source}"
    else:
        clear_animation(objs)
        explode = lambda level: None
        curve, levels, frames, turn, name = None, [0.0], FRAMES, 360, "turntable"
    clouds = normalize_model(model, meshes, levels, explode)
    if curve:
        bake_assembly(objs, curve, explode)
    setup_normals(meshes)
    setup_materials(meshes)
    target, radius = setup_camera(levels, clouds, curve)
    build_cyclorama(radius)
    setup_world()
    setup_lights(target, radius, meshes)
    animate_turntable(turntable, frames, turn)
    setup_render(mode)

    if mode == "blend":
        path = ROOT / OUTPUT_DIR / name / "scene.blend"
        path.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        log(f"Szene gespeichert: {path}")
    else:
        test_frames = [int(f) for f in arg("--frames", "").split(",") if f] or TEST_FRAMES   # z. B. 1,45,90
        render(mode, "--resume" in args, test_frames, name)


if __name__ == "__main__":
    main()
