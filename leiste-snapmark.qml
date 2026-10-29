import QtQuick

// Snapmark-Knopf für die Omarchy-Leiste: Pixel-S im Rahmen in der Leistenfarbe.
// Linksklick = Bereich, Rechtsklick = ganzer Bildschirm.
Item {
  id: root
  property var bar
  property string moduleName
  property var settings

  implicitWidth: 26
  implicitHeight: bar ? bar.barSize : 26

  // x, y, Breite, Höhe auf dem 16er-Raster (wie snapmark.svg)
  readonly property var pixels: [
    [0, 0, 16, 1], [0, 15, 16, 1], [0, 1, 1, 14], [15, 1, 1, 14],
    [5, 3, 7, 2], [4, 5, 2, 2], [5, 7, 6, 2], [10, 9, 2, 2], [4, 11, 7, 2]
  ]

  Item {
    anchors.centerIn: parent
    width: 16
    height: 16
    opacity: mouse.containsMouse ? 1.0 : 0.85

    Repeater {
      model: root.pixels
      Rectangle {
        x: modelData[0]; y: modelData[1]; width: modelData[2]; height: modelData[3]
        color: root.bar ? root.bar.foreground : "white"
      }
    }
  }

  MouseArea {
    id: mouse
    anchors.fill: parent
    hoverEnabled: true
    acceptedButtons: Qt.LeftButton | Qt.RightButton
    cursorShape: Qt.PointingHandCursor
    onClicked: (e) => {
      if (!bar) return
      bar.hideTooltip(root)
      bar.run(e.button === Qt.RightButton ? "~/.local/bin/snapmark fullscreen" : "~/.local/bin/snapmark")
    }
    onEntered: if (bar) bar.showTooltip(root, "Snapmark: Klick = Bereich, Rechtsklick = ganzer Bildschirm")
    onExited: if (bar) bar.hideTooltip(root)
  }
}
