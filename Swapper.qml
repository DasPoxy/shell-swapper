import Quickshell
import Quickshell.Wayland
import QtQuick
import qs.Commons
import qs.Ui

// The picker: your shell loadouts in a card in the middle of the screen.
//   omarchy-shell shell toggle shell-swapper '{}'
Item {
  id: root

  property var shell: null
  property var manifest: null
  property bool opened: false

  function open(payloadJson) {
    root.opened = true
    view.refresh()
    Qt.callLater(function() { view.takeKeys() })
  }

  function close() {
    root.opened = false
  }

  function dismiss() {
    root.opened = false
    if (root.shell && typeof root.shell.hide === "function")
      root.shell.hide((root.manifest && root.manifest.id) || "shell-swapper")
  }

  function toggle() {
    if (root.opened) root.dismiss()
    else root.open("{}")
  }

  PanelWindow {
    id: panel
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "shell-swapper"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
    exclusionMode: ExclusionMode.Ignore

    Rectangle {
      anchors.fill: parent
      color: Color.menu.scrim
    }

    // a click off the card closes it
    MouseArea {
      anchors.fill: parent
      onClicked: root.dismiss()
    }

    BorderSurface {
      id: card
      width: Math.min(Style.space(940), panel.width - Style.gapsOut * 2 - 60)
      height: Math.min(Style.space(600), panel.height - Style.gapsOut * 2 - 60)
      anchors.centerIn: parent
      radius: Style.cornerRadius
      color: view.solid
      borderSpec: Border.surfaceSpec("menu", "border", Color.menu.border, Math.max(1, Style.space(2)))
      padding: Style.spacing.panelPadding

      MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onClicked: {} }

      SwapperView {
        id: view
        anchors.fill: parent
        anchors.topMargin: card.contentTopInset
        anchors.rightMargin: card.contentRightInset
        anchors.bottomMargin: card.contentBottomInset
        anchors.leftMargin: card.contentLeftInset
        active: root.opened
        closeRequest: function() { root.dismiss() }
      }
    }
  }
}
