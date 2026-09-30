# Produktvideo aus CAD

Aus einem 3D-/CAD-Modell entstehen per Script nahtlose Web-Loops: eine 360°-Drehung und ein Zusammenbau, bei dem die Teile an ihren Platz fliegen. Alles läuft headless in Blender (Cycles), ffmpeg macht daraus WebM und MP4.

**Demo-Seite:** https://luuc4.github.io/cad-render-demo/

| Variante | Woher die Bewegung kommt |
|---|---|
| Drehung | 360° um die Hochachse, geht mit jedem Modell |
| Zusammenbau, Animation aus der Datei | Explosionsansicht, die im CAD angelegt und als Animation exportiert wurde (mechanisch korrekt) |
| Zusammenbau, automatisch | Wege aus der Form der Teile berechnet: Schrauben längs, Deckel/Scheiben senkrecht zur Fläche, sonst von der Mitte weg |

## Was die Datei können muss

- **Jedes Bauteil als eigenes Objekt.** Eine STL, in der alles zu einem Körper verschmolzen ist, taugt nur für die Drehung, nicht für den Zusammenbau.
- Formate: FBX, glTF/GLB, OBJ, STL, PLY. STEP vorher umwandeln (z. B. FreeCAD).
- Für einen mechanisch korrekten Zusammenbau: Explosionsansicht als Animation in der Datei (z. B. FBX). Ohne Animation wird automatisch berechnet.

## Benutzung

Voraussetzungen: Blender 5.x, ffmpeg mit libvpx-vp9 und libx264. Modell unter `source/` ablegen und `MODEL_PATH` in `render_setup.py` anpassen.

```sh
BLENDER=/Applications/Blender.app/Contents/MacOS/Blender

# Einzelbilder zum Look-Check (960×540)
$BLENDER --background --factory-startup --python render_setup.py -- --test

# Ganze Sequenz: --preview (960×540), --demo (1280×720) oder --final (1920×1080)
$BLENDER --background --factory-startup --python render_setup.py -- --preview --anim turntable
$BLENDER --background --factory-startup --python render_setup.py -- --preview --anim assembly --explode file
$BLENDER --background --factory-startup --python render_setup.py -- --preview --anim assembly --explode auto

# PNG-Sequenz -> WebM + MP4 + Poster
./encode_web.sh renders/assembly-file/preview/frames renders/assembly-file/preview/web radial-engine-assembly-file
```

Alle Stellschrauben (Ausrichtung, Materialfarben, Licht, Kamera, Timing der Phasen, Qualitätsstufen, Schatten) stehen als Variablen oben in `render_setup.py`.

Ergebnis: Hintergrund pixelgenau `BG_COLOR` (Standard `#FFFFFF`), dadurch keine sichtbare Videokante auf der Website. Zusätzlich liegt jede Sequenz transparent in `.../frames_alpha/`.

## Einbinden

```html
<video autoplay muted loop playsinline poster="motor-poster.jpg">
  <source src="motor.webm" type="video/webm">
  <source src="motor.mp4" type="video/mp4">
</video>
```

## Modell und Lizenz

Modell: [3d Printable Radial Pneumatic Engine](https://sketchfab.com/3d-models/3d-printable-radial-pneumatic-engine-3cbddbecd6c5462391e9936a3ccd7d32) von [Slava Z.](https://sketchfab.com/slava), lizenziert unter [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Verändert: Materialien, Ausrichtung, Animation neu getaktet bzw. neu berechnet. Die Modelldatei ist nicht Teil dieses Repos.

Studio-HDRI `studio_small_09` von [Poly Haven](https://polyhaven.com/a/studio_small_09) (CC0), wird beim ersten Lauf automatisch geladen.
