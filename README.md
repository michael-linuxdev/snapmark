# Snapmark für Omarchy

Bildschirmfoto aufnehmen und sofort beschriften. Ein Python/GTK4-Skript, nutzt Omarchys Bereichsauswahl.

    snapmark [smart|region|windows|fullscreen]   aufnehmen, dann Editor
    snapmark DATEI.png                           vorhandenes Bild bearbeiten

Werkzeuge: Esc Auswählen, R Rechteck, E Ellipse, L Linie, A Pfeil, F Freihand, T Text,
H Markieren, O Unkenntlich, I Zähler, C Zuschneiden (Enter übernimmt).
Strg+Z/Y, Strg+C Zwischenablage, Strg+S Bilderordner, Strg+Umschalt+S Speichern unter, Strg+Plus/Minus/0 Zoom.

Einbau: `ln -s $PWD/snapmark.py ~/.local/bin/snapmark`, Symbol nach `~/.local/share/icons/hicolor/scalable/apps/org.mkm.snapmark.svg`, Leistenknopf `leiste-snapmark.qml` nach `~/.config/omarchy/bar/modules/snapmark.qml` (in `shell.json`: `{"id":"snapmark","type":"qml"}`), Belegung und Fensterregel in `~/.config/hypr/`.
