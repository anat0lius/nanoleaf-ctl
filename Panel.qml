import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

Panel {
  id: root
  moduleName: "nanoleaf"
  ipcTarget: "nanoleaf"
  manageIpc: true

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // The controller ships inside this plugin: run it with the system python, no install step.
  readonly property string pluginDir: decodeURIComponent(Qt.resolvedUrl(".").toString().replace("file://", "")).replace(/\/$/, "")

  function ctl(args) {
    return ["env", "PYTHONPATH=" + root.pluginDir + "/src", "python3", "-m", "nanoleaf_ctl"].concat(args)
  }

  Component.onCompleted: {
    root.refresh()
    // Restore the last state once per login (the shell starts with the session).
    runScript(["session-start", "--once"])
  }

  property bool configured: true
  // After pairing, the setup card asks for a display name before showing the controls.
  property bool namingStep: false
  property bool nameEdited: false
  property string rawDeviceName: ""
  readonly property bool ready: root.configured && !root.namingStep
  property bool canMirror: false
  property var themePalette: []
  property string themeSlug: ""
  // Theme sync is offered only when the desktop theme's name and palette can be read.
  readonly property bool themeAvailable: root.themePalette.length > 0 && root.themeSlug !== ""
  property bool pairing: false
  property string pairMessage: ""

  property bool isOnline: false
  property bool isOn: false
  property int brightness: 0
  property string currentScene: ""
  property var scenes: []
  property var musicScenes: []
  property string deviceName: "Nanoleaf"
  property string model: ""
  property bool isMirroring: false
  property string mirrorDisplay: ""
  property var displays: []
  property int currentCt: 0
  property string colorMode: ""
  property bool isThemeSynced: false
  property string themeName: ""

  readonly property color themeForeground: (root.bar && root.bar.barForeground) ? root.bar.barForeground : Color.foreground

  readonly property string icon: {
    if (!isOnline) return "󰌶"
    return isOn ? "󰌵" : "󰌶"
  }

  function runScript(args) {
    Quickshell.execDetached(root.ctl(args))
  }

  function themeArgs(command, slug) {
    return [command, "--name", slug, "--colors"].concat(root.themePalette)
  }

  // Palette colors from a theme's colors.toml, in a fixed order, without duplicates.
  function parsePalette(text) {
    var found = {}
    String(text || "").split("\n").forEach(function(line) {
      var m = line.match(/^\s*([A-Za-z_]+)\s*=\s*["']?(#[0-9a-fA-F]{3,6})["']?\s*$/)
      if (m) found[m[1]] = m[2]
    })
    var out = []
    ;["accent", "blue", "cyan", "green", "yellow", "orange", "magenta", "red"].forEach(function(k) {
      if (found[k] && out.indexOf(found[k]) === -1) out.push(found[k])
    })
    return out
  }

  function finishNaming() {
    var name = nameField.text.trim()
    // Only store a name that differs from the device's own, so renames in the Nanoleaf app still show.
    if (name !== "" && name !== root.rawDeviceName) {
      root.deviceName = name
      runScript(["rename", name])
    }
    root.namingStep = false
    statusDelayTimer.restart()
  }

  onRawDeviceNameChanged: if (root.namingStep && !root.nameEdited) nameField.text = root.rawDeviceName

  function startPairing() {
    if (pairProc.running) return
    root.pairing = true
    root.pairMessage = ""
    pairProc.running = true
  }

  function refresh() {
    if (!statusProc.running) statusProc.running = true
  }

  function togglePower() {
    if (!root.configured) return
    root.isOn = !root.isOn
    if (!root.isOn) root.isMirroring = false
    runScript(["toggle"])
    statusDelayTimer.restart()
  }

  function setBrightness(val) {
    root.brightness = Math.max(1, Math.min(100, Math.round(val)))
    runScript(["brightness", String(root.brightness)])
  }

  function toggleMirror() {
    root.isMirroring = !root.isMirroring
    if (root.isMirroring) {
      root.isOn = true
      root.isThemeSynced = false
      runScript(root.mirrorDisplay ? ["mirror", "start", "--display", root.mirrorDisplay] : ["mirror", "start"])
    } else {
      runScript(["mirror", "stop"])
    }
    statusDelayTimer.restart()
  }

  function setMirrorDisplay(disp) {
    if (root.mirrorDisplay === disp && root.isMirroring) return
    root.mirrorDisplay = disp
    if (root.isMirroring) {
      runScript(["mirror", "start", "--display", disp])
      statusDelayTimer.restart()
    }
  }

  function selectScene(name) {
    root.isMirroring = false
    root.isThemeSynced = false
    root.colorMode = "effect"
    root.currentScene = name
    root.isOn = true
    runScript(["scene", name])
    statusDelayTimer.restart()
  }

  function setCt(kelvin) {
    root.isMirroring = false
    root.isThemeSynced = false
    root.colorMode = "ct"
    root.currentCt = kelvin
    root.currentScene = ""
    root.isOn = true
    runScript(["ct", String(kelvin)])
    statusDelayTimer.restart()
  }

  function toggleThemeSync() {
    if (root.isThemeSynced) {
      root.isThemeSynced = false
      runScript(["theme-sync", "--off"])
    } else {
      root.isMirroring = false
      root.colorMode = "effect"
      root.currentCt = 0
      root.isThemeSynced = true
      root.isOn = true
      runScript(root.themeArgs("theme-sync", root.themeSlug))
    }
    statusDelayTimer.restart()
  }

  onOpenedChanged: {
    if (opened) refresh()
  }

  Timer {
    id: pollTimer
    interval: 10000
    running: true
    repeat: true
    onTriggered: root.refresh()
  }

  Timer {
    id: initialTimer
    interval: 600
    running: true
    repeat: false
    onTriggered: root.refresh()
  }

  Timer {
    id: statusDelayTimer
    interval: 500
    repeat: false
    onTriggered: root.refresh()
  }

  Timer {
    id: brightnessDebounce
    interval: 200
    repeat: false
    onTriggered: root.setBrightness(root.brightness)
  }

  Process {
    id: statusProc
    command: root.ctl(["status", "--json"])
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var raw = String(text || "").trim()
        if (!raw) return
        try {
          var data = JSON.parse(raw)
          root.configured = data.configured !== false
          if (!root.configured) {
            root.isOnline = false
            return
          }
          root.isOnline = true
          root.rawDeviceName = data.rawName || data.name || ""
          root.isOn = !!data.on
          root.brightness = (data.brightness !== undefined) ? data.brightness : 0
          root.currentScene = data.currentEffect || ""
          root.scenes = data.effectsList || []
          root.musicScenes = data.musicScenes || []
          root.deviceName = data.name || "Nanoleaf"
          root.model = data.model || ""
          root.currentCt = (data.ct !== undefined) ? data.ct : 0
          root.colorMode = data.colorMode || ""
          if (root.colorMode === "ct") {
            root.currentScene = ""
          }
          root.isMirroring = !!(data.mirror && data.mirror.active)
          root.displays = data.displays || []
          root.canMirror = !!(data.capabilities && data.capabilities.mirror)
          if (data.mirror && data.mirror.display) root.mirrorDisplay = data.mirror.display
          if (data.themeSync) {
            root.isThemeSynced = !!data.themeSync.synced
            root.themeName = data.themeSync.theme || ""
          }
        } catch (e) {
          // JSON parse failed or error returned
        }
      }
    }
  }

  Process {
    id: pairProc
    command: root.ctl(["pair", "--timeout", "60"])
    stdout: StdioCollector {
      id: pairOut
      waitForEnd: true
    }
    onExited: function(exitCode) {
      root.pairing = false
      if (exitCode === 0) {
        root.pairMessage = ""
        root.nameEdited = false
        root.namingStep = true
        root.refresh()
      } else {
        var lines = String(pairOut.text || "").trim().split("\n")
        root.pairMessage = lines[lines.length - 1] || "Pairing failed."
      }
    }
  }

  // Follow the desktop theme (Omarchy): lock the lights to its palette, or preview it briefly.
  // The theme name file is written after the new colors are in place, so a change to it means
  // colors.toml is already current.
  property string pendingThemeSlug: ""

  FileView {
    id: themeFile
    path: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme.name"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var slug = String(text() || "").trim()
      var previous = root.themeSlug
      root.themeSlug = slug
      // The first read is just startup, not a theme change.
      if (previous && slug && slug !== previous) {
        root.pendingThemeSlug = slug
        colorsFile.reload()
      }
    }
    onLoadFailed: root.themeSlug = ""
  }

  FileView {
    id: colorsFile
    path: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme/colors.toml"
    printErrors: false
    onLoaded: {
      root.themePalette = root.parsePalette(text())
      if (root.pendingThemeSlug && root.themeAvailable && root.configured) {
        root.runScript(root.themeArgs("theme-change", root.pendingThemeSlug))
        statusDelayTimer.restart()
      }
      root.pendingThemeSlug = ""
    }
    onLoadFailed: root.themePalette = []
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.icon
    active: root.isOn
    activeColor: Color.accent
    dimmed: !root.isOn
    slotSize: Style.bar.iconSlot
    tooltipText: !root.configured ? "Nanoleaf: Not set up" : root.isOnline
      ? (root.deviceName + ": " + (root.isOn ? (root.isMirroring ? ("Mirroring " + root.mirrorDisplay) : (root.isThemeSynced ? ("Theme: " + root.themeName) : ((root.currentScene ? root.currentScene + " · " : "") + root.brightness + "%"))) : "Off"))
      : "Nanoleaf: Offline"

    onPressed: function(b) {
      if (b === Qt.RightButton) {
        root.togglePower()
      } else {
        root.toggle()
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(340))
    contentHeight: panel.fittedContentHeight(mainColumn.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      // The name field owns the keyboard while it is shown.
      blocked: root.namingStep
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: mainColumn
        width: parent.width
        spacing: Style.space(12)

        // ---------- First run: pairing, then naming ----------
        Column {
          width: parent.width
          spacing: Style.space(8)
          visible: !root.ready

          PanelSectionHeader {
            text: root.configured ? "NAME YOUR LIGHTS" : "SET UP NANOLEAF"
            foreground: root.themeForeground
            fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
          }

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.configured
              ? "Paired! Choose the name shown in the bar."
              : (root.pairing
                ? "Hold the power button on the controller for 5-7 seconds, until the LED starts flashing, then release it."
                : "Pair this computer with your Nanoleaf controller. It must be on the same network.")
            color: root.themeForeground
            font.pixelSize: Style.font.body
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
          }

          Text {
            width: parent.width
            visible: !root.configured && root.pairMessage !== ""
            wrapMode: Text.WordWrap
            text: root.pairMessage
            color: Color.accent
            font.pixelSize: Style.font.caption
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
          }

          Button {
            width: parent.width
            visible: !root.configured
            text: root.pairing ? "Waiting for the controller…" : (root.pairMessage ? "Try again" : "Pair")
            selected: root.pairing
            fontSize: Style.font.caption
            bordered: true
            foreground: root.themeForeground
            onClicked: root.startPairing()
          }

          TextField {
            id: nameField
            width: parent.width
            visible: root.configured && root.namingStep
            placeholderText: root.rawDeviceName || "Nanoleaf"
            foreground: root.themeForeground
            onTextEdited: root.nameEdited = true
            onAccepted: root.finishNaming()
            Keys.onEscapePressed: root.close()
            onVisibleChanged: if (visible) {
              text = root.rawDeviceName
              Qt.callLater(forceActiveFocus)
            }
          }

          Button {
            width: parent.width
            visible: root.configured && root.namingStep
            text: "Continue"
            fontSize: Style.font.caption
            bordered: true
            foreground: root.themeForeground
            onClicked: root.finishNaming()
          }
        }

        // ---------- Hero: Icon, Name & Toggle ----------
        Item {
          visible: root.ready
          width: parent.width
          implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight, powerSwitch.implicitHeight)

          Text {
            id: heroIcon
            textFormat: Text.PlainText
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: root.icon
            color: root.themeForeground
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.display
            opacity: root.isOn ? 1.0 : 0.5
          }

          Column {
            id: heroLabels
            anchors.left: heroIcon.right
            anchors.leftMargin: Style.space(12)
            anchors.right: powerSwitch.left
            anchors.rightMargin: Style.space(12)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              text: root.deviceName
              color: root.themeForeground
              font.family: root.bar ? root.bar.fontFamily : Style.font.family
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
              width: parent.width
            }

            Text {
              textFormat: Text.PlainText
              text: {
                if (!root.isOnline) return "OFFLINE"
                if (!root.isOn) return "POWER OFF"
                if (root.isMirroring) return ("MIRRORING " + root.mirrorDisplay).toUpperCase()
                if (root.isThemeSynced) return ("THEME: " + (root.themeName || "THEME")).toUpperCase()
                if (root.colorMode === "ct" || !root.currentScene) {
                  return ("WHITE " + (root.currentCt > 0 ? (root.currentCt + "K") : "LIGHT")).toUpperCase()
                }
                return (root.currentScene || "LIGHT ON").toUpperCase()
              }
              color: root.isOn ? Color.accent : Qt.darker(root.themeForeground || Color.foreground, 1.4)
              font.family: root.bar ? root.bar.fontFamily : Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              elide: Text.ElideRight
              width: parent.width
            }
          }

          ToggleSwitch {
            id: powerSwitch
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            checked: root.isOn
            foreground: root.themeForeground
            onToggled: root.togglePower()
          }
        }

        PanelSeparator {
          visible: root.ready
          foreground: root.themeForeground
        }

        // ---------- Brightness Slider ----------
        Column {
          visible: root.ready
          width: parent.width
          spacing: Style.space(6)
          opacity: root.isOn ? 1.0 : 0.5

          Row {
            width: parent.width

            Text {
              text: "󰃠"
              color: root.themeForeground
              font.pixelSize: Style.font.icon
              font.family: root.bar ? root.bar.fontFamily : Style.font.family
              anchors.verticalCenter: parent.verticalCenter
            }

            Item { width: Style.space(8); height: 1 }

            Text {
              text: "Brightness"
              color: root.themeForeground
              font.pixelSize: Style.font.body
              font.family: root.bar ? root.bar.fontFamily : Style.font.family
              anchors.verticalCenter: parent.verticalCenter
            }

            Item {
              width: parent.width - Style.space(140)
              height: 1
            }

            Text {
              text: root.brightness + "%"
              color: Qt.darker(root.themeForeground || Color.foreground, 1.3)
              font.pixelSize: Style.font.caption
              font.family: root.bar ? root.bar.fontFamily : Style.font.family
              font.bold: true
              anchors.verticalCenter: parent.verticalCenter
              horizontalAlignment: Text.AlignRight
            }
          }

          PanelSlider {
            id: brightnessSlider
            width: parent.width
            bar: root.bar
            minimum: 1
            maximum: 100
            step: 1
            integer: true
            value: root.brightness
            onMoved: function(v) {
              root.brightness = Math.round(v)
              brightnessDebounce.restart()
            }
            onReleased: function(v) {
              root.brightness = Math.round(v)
              brightnessDebounce.stop()
              root.setBrightness(root.brightness)
            }
          }
        }

        PanelSeparator {
          visible: root.ready
          foreground: root.themeForeground
        }

        // ---------- Color Temperature Quick Buttons ----------
        Column {
          visible: root.ready
          width: parent.width
          spacing: Style.space(8)
          opacity: root.isOn ? 1.0 : 0.5

          PanelSectionHeader {
            text: "WHITE TEMPERATURE"
            foreground: root.themeForeground
            fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
          }

          Row {
            width: parent.width
            spacing: Style.space(6)

            Repeater {
              model: [
                { label: "Warm", k: 2700 },
                { label: "Soft", k: 3500 },
                { label: "Neutral", k: 4500 },
                { label: "Cool", k: 6500 }
              ]

              Button {
                required property var modelData
                width: (parent.width - Style.space(18)) / 4
                text: modelData.label
                selected: !root.isMirroring && root.isOn && (root.colorMode === "ct" || !root.currentScene) && Math.abs(root.currentCt - modelData.k) < 300
                fontSize: Style.font.caption
                bordered: true
                foreground: root.themeForeground
                onClicked: root.setCt(modelData.k)
              }
            }
          }
        }

        PanelSeparator {
          visible: root.ready && root.canMirror
          foreground: root.themeForeground
        }

        // ---------- Screen Mirror Section ----------
        Column {
          visible: root.ready && root.canMirror
          width: parent.width
          spacing: Style.space(8)
          opacity: root.isOn ? 1.0 : 0.5

          Row {
            width: parent.width

            Column {
              anchors.verticalCenter: parent.verticalCenter
              width: parent.width - mirrorSwitch.width - Style.space(12)
              spacing: Style.space(2)

              Row {
                spacing: Style.space(6)

                Text {
                  text: "󰍹"
                  color: root.isMirroring ? Color.accent : root.themeForeground
                  font.pixelSize: Style.font.icon
                  font.family: root.bar ? root.bar.fontFamily : Style.font.family
                  anchors.verticalCenter: parent.verticalCenter
                }

                Text {
                  text: "Screen Mirror"
                  color: root.isMirroring ? Color.accent : root.themeForeground
                  font.pixelSize: Style.font.body
                  font.family: root.bar ? root.bar.fontFamily : Style.font.family
                  font.bold: root.isMirroring
                  anchors.verticalCenter: parent.verticalCenter
                }
              }

              Text {
                text: root.isMirroring ? ("Active on " + root.mirrorDisplay) : "Sync lighting with display"
                color: Qt.darker(root.themeForeground || Color.foreground, 1.4)
                font.pixelSize: Style.font.caption
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
              }
            }

            ToggleSwitch {
              id: mirrorSwitch
              anchors.verticalCenter: parent.verticalCenter
              checked: root.isMirroring
              foreground: root.themeForeground
              onToggled: root.toggleMirror()
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(6)
            visible: root.displays.length > 1

            Repeater {
              model: root.displays

              Button {
                required property var modelData
                width: (parent.width - Style.space(6) * (root.displays.length - 1)) / Math.max(1, root.displays.length)
                text: modelData
                selected: root.mirrorDisplay === modelData
                fontSize: Style.font.caption
                bordered: true
                foreground: root.themeForeground
                onClicked: root.setMirrorDisplay(modelData)
              }
            }
          }
        }

        PanelSeparator {
          visible: root.ready && root.themeAvailable
          foreground: root.themeForeground
        }

        // ---------- Theme Sync Section ----------
        Column {
          visible: root.ready && root.themeAvailable
          width: parent.width
          spacing: Style.space(8)
          opacity: root.isOn ? 1.0 : 0.5

          Row {
            width: parent.width

            Row {
              spacing: Style.space(6)
              anchors.verticalCenter: parent.verticalCenter

              Text {
                text: "󰏘"
                color: root.isThemeSynced ? Color.accent : root.themeForeground
                font.pixelSize: Style.font.icon
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                anchors.verticalCenter: parent.verticalCenter
              }

              Text {
                text: "Theme Sync"
                color: root.isThemeSynced ? Color.accent : root.themeForeground
                font.pixelSize: Style.font.body
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.bold: root.isThemeSynced
                anchors.verticalCenter: parent.verticalCenter
              }
            }

            Item {
              width: parent.width - Style.space(160)
              height: 1
            }

            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: root.isThemeSynced ? "Locked" : ""
              color: root.isThemeSynced ? Color.accent : Qt.darker(root.themeForeground || Color.foreground, 1.4)
              font.pixelSize: Style.font.caption
              font.family: root.bar ? root.bar.fontFamily : Style.font.family
              font.bold: root.isThemeSynced
            }
          }

          Button {
            width: parent.width
            text: root.isThemeSynced
              ? ("Synced to " + (root.themeName ? root.themeName : "Theme") + " (Click to Unlock)")
              : ("Lock to " + (root.themeName ? root.themeName : "Theme") + " Palette")
            selected: root.isThemeSynced
            fontSize: Style.font.caption
            bordered: true
            foreground: root.themeForeground
            onClicked: root.toggleThemeSync()
          }
        }

        PanelSeparator {
          visible: root.ready
          foreground: root.themeForeground
        }

        // ---------- Scenes Section ----------
        Column {
          visible: root.ready
          width: parent.width
          spacing: Style.space(8)

          PanelSectionHeader {
            text: "SCENES"
            foreground: root.themeForeground
            fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
          }

          ListView {
            id: scenesListView
            width: parent.width
            height: Math.min(contentHeight, Style.space(320))
            spacing: Style.space(2)
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height
            model: root.scenes

            ScrollBar.vertical: ScrollBar {
              policy: ScrollBar.AsNeeded
            }

            delegate: CursorSurface {
              id: sceneRow
              required property var modelData
              required property int index
              width: scenesListView.width
              height: Style.space(24)
              current: modelData === root.currentScene
              foreground: root.themeForeground
              accent: Color.accent

              Text {
                anchors.left: parent.left
                anchors.leftMargin: Style.space(10)
                anchors.right: musicIcon.visible ? musicIcon.left : parent.right
                anchors.rightMargin: Style.space(10)
                anchors.verticalCenter: parent.verticalCenter
                text: modelData
                color: modelData === root.currentScene ? sceneRow.accent : sceneRow.foreground
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: Style.font.body
                font.bold: modelData === root.currentScene
                elide: Text.ElideRight
              }

              Text {
                id: musicIcon
                visible: root.musicScenes.indexOf(modelData) !== -1
                anchors.right: parent.right
                anchors.rightMargin: Style.space(10)
                anchors.verticalCenter: parent.verticalCenter
                text: "󰝚"
                color: modelData === root.currentScene ? sceneRow.accent : sceneRow.foreground
                opacity: 0.7
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: Style.font.body
              }

              MouseArea {
                id: sceneMouse
                anchors.fill: parent
                hoverEnabled: true
                onEntered: sceneRow.hasCursor = true
                onExited: sceneRow.hasCursor = false
                onClicked: root.selectScene(modelData)
              }
            }
          }
        }
      }
    }
  }
}
