import QtQuick

// Snapmark-Knopf: Linksklick = Bereich, Rechtsklick = ganzer Bildschirm
Item {
  property var bar
  property string moduleName
  property var settings

  implicitWidth: 26
  implicitHeight: bar ? bar.barSize : 26

  Image {
    anchors.centerIn: parent
    source: "file:///home/michael/Projects/snapmark/snapmark.svg"
    sourceSize.width: 16
    sourceSize.height: 16
    opacity: mouse.containsMouse ? 1.0 : 0.92
  }

  MouseArea {
    id: mouse
    anchors.fill: parent
    hoverEnabled: true
    acceptedButtons: Qt.LeftButton | Qt.RightButton
    cursorShape: Qt.PointingHandCursor
    onClicked: (e) => {
      if (!bar) return
      bar.hideTooltip(parent)
      bar.run(e.button === Qt.RightButton ? "/home/michael/.local/bin/snapmark fullscreen" : "/home/michael/.local/bin/snapmark")
    }
    onEntered: if (bar) bar.showTooltip(parent, "Snapmark: Klick = Bereich, Rechtsklick = ganzer Bildschirm")
    onExited: if (bar) bar.hideTooltip(parent)
  }
}
