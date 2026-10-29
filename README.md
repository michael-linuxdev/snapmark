# Greenshot für Omarchy

Bildschirmfoto aufnehmen und sofort beschriften. Ein Python/GTK4-Skript, nutzt Omarchys Bereichsauswahl.

    greenshot [smart|region|windows|fullscreen]   aufnehmen, dann Editor
    greenshot DATEI.png                           vorhandenes Bild bearbeiten

Werkzeuge: Esc Auswählen, R Rechteck, E Ellipse, L Linie, A Pfeil, F Freihand, T Text,
H Markieren, O Unkenntlich, I Zähler, C Zuschneiden (Enter übernimmt).
Strg+Z/Y, Strg+C Zwischenablage, Strg+S Bilderordner, Strg+Umschalt+S Speichern unter, Strg+Plus/Minus/0 Zoom.

Einbau: `ln -s $PWD/greenshot.py ~/.local/bin/greenshot`, Belegung und Fensterregel in `~/.config/hypr/`.
