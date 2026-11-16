import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    visible: true
    width: 1180
    height: 720
    minimumWidth: 960
    minimumHeight: 600
    title: qsTr("PyPlayer")

    property int selectedPlaylist: -1
    property string selectedPlaylistName: ""
    property string currentPlaylistName: ""
    property string currentTitle: ""
    property string currentMode: "sequential"
    property string statusText: ""
    property string propsTitle: ""
    property string propsDetails: ""

    function formatTime(seconds) {
        var total = Math.max(0, Math.floor(seconds || 0))
        var mins = Math.floor(total / 60)
        var secs = total % 60
        return mins + ":" + (secs < 10 ? "0" : "") + secs
    }

    function formatStats(stat) {
        if (!stat || !stat.trackCount)
            return qsTr("Выберите плейлист, чтобы увидеть статистику")
        return qsTr("Треков: %1 · Общая длительность: %2\n").arg(
                   stat.trackCount).arg(formatTime(stat.totalDurationSec))
                + qsTr("Средняя: %1 · Мин: %2 · Макс: %3\n").arg(
                   formatTime(stat.avgDurationSec)).arg(
                   formatTime(stat.minDurationSec)).arg(
                   formatTime(stat.maxDurationSec))
                + qsTr("Прослушано: %1 · Доля: %2%").arg(
                   formatTime(stat.listenedSec)).arg(
                   stat.sharePercent.toFixed(1))
    }

    // Единственный источник правды о режиме порядка — PlaybackController:
    // он обновляет активную очередь и сохраняет режим в БД целевого
    // плейлиста. Модель плейлистов здесь только перечитывается из БД.
    function applyMode(mode) {
        root.currentMode = mode
        player.set_order_mode(mode,
                              root.selectedPlaylist > 0
                              ? root.selectedPlaylist : -1)
        playlistModel.reload()
        root.refreshTrackLabels()
    }

    function refreshTrackLabels() {
        prevLabel.text = player.prev_track_label()
        nextLabel.text = player.next_track_label()
        currentDetails.text = player.current_track_details()
    }

    // Заглушка обложки: в БД пока нет изображения, Image оставлен как
    // точка подключения обложки из карточки трека в будущем.
    component TrackIcon: Item {
        property string glyph: "♪"
        width: 36
        height: 36
        Rectangle {
            anchors.fill: parent
            color: "#e6edf7"
            radius: 4
        }
        Image {
            anchors.fill: parent
            fillMode: Image.PreserveAspectFit
            source: ""
            visible: false
        }
        Text {
            anchors.centerIn: parent
            text: parent.glyph
            color: "#7a8ca8"
            font.pixelSize: 18
        }
    }

    header: ToolBar {
        RowLayout {
            anchors.fill: parent
            Label {
                text: qsTr("PyPlayer")
                font.bold: true
                font.pixelSize: 16
            }
            Item { Layout.fillWidth: true }
            Label { text: qsTr("Умная рандомизация") }
            ComboBox {
                id: smartShuffleBox
                model: [qsTr("Выкл"), qsTr("Прямая"), qsTr("Обратная")]
                property var values: ["off", "direct", "inverse"]
                currentIndex: Math.max(0, values.indexOf(settings.smartShuffle))
                onActivated: settings.setSmartShuffle(values[currentIndex])
            }
            Button {
                text: qsTr("Настройки")
                onClicked: settingsPopup.open()
            }
        }
    }

    footer: Label {
        text: root.statusText || qsTr("Готово")
        padding: 4
        background: Rectangle { color: "#f4f4f4" }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 8

        // ---------- Колонка 1: плейлисты и треки ----------
        ColumnLayout {
            Layout.preferredWidth: 330
            Layout.fillHeight: true
            spacing: 4

            Label {
                text: qsTr("Плейлисты и треки")
                font.bold: true
                font.pixelSize: 16
            }
            RowLayout {
                Button {
                    text: qsTr("Создать")
                    onClicked: createPopup.open()
                }
                Button {
                    text: qsTr("Переименовать")
                    enabled: root.selectedPlaylist > 0
                    onClicked: {
                        renameField.text = root.selectedPlaylistName
                        renamePopup.open()
                    }
                }
                Button {
                    text: qsTr("Удалить")
                    enabled: root.selectedPlaylist > 0
                    onClicked: {
                        playlistModel.remove(root.selectedPlaylist)
                        root.selectedPlaylist = -1
                        root.selectedPlaylistName = ""
                        playlistItems.setPlaylist(-1)
                        statsText.text = ""
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                border.color: "#bbbbbb"
                radius: 3
                // DropArea — родитель списка: Qt доставляет drop по цепочке
                // родителей, а не соседним элементам.
                DropArea {
                    anchors.fill: parent
                    onDropped: {
                        var tid = Number(drop.getDataAsString(
                                             "application/x-pyplayer-track"))
                        if (tid > 0 && root.selectedPlaylist > 0)
                            playlistItems.addTrack(tid)
                        else if (tid > 0)
                            root.statusText = qsTr("Сначала выберите плейлист")
                    }
                    ListView {
                        id: playlistsView
                        anchors.fill: parent
                        anchors.margins: 1
                        clip: true
                        model: playlistModel
                        delegate: Rectangle {
                            width: playlistsView.width
                            height: 64
                            color: root.selectedPlaylist === model.playlistId
                                   ? "#d9e8ff" : (plistMouse.containsMouse
                                                  ? "#f2f2f2" : "white")
                            border.color: "#dddddd"
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 4
                                spacing: 0
                                Text {
                                    text: model.name
                                    font.bold: true
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: qsTr("Треков: %1 · %2").arg(
                                              model.trackCount).arg(
                                              model.createdAt)
                                    font.pixelSize: 11
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: model.description
                                    font.pixelSize: 11
                                    color: "#666666"
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                            }
                            MouseArea {
                                id: plistMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.LeftButton | Qt.RightButton
                                onClicked: {
                                    if (mouse.button === Qt.RightButton) {
                                        plistMenu.popup()
                                        return
                                    }
                                    root.selectedPlaylist = model.playlistId
                                    root.selectedPlaylistName = model.name
                                    playlistItems.setPlaylist(model.playlistId)
                                    root.currentMode = model.orderMode
                                    statsText.text = root.formatStats(
                                        playlistModel.stats(model.playlistId))
                                    root.refreshTrackLabels()
                                }
                                onDoubleClicked: player.play_playlist(
                                                     model.playlistId)
                            }
                            Menu {
                                id: plistMenu
                                MenuItem {
                                    text: qsTr("Воспроизвести")
                                    onTriggered: player.play_playlist(
                                                     model.playlistId)
                                }
                            }
                        }
                    }
                }
            }

            Label {
                text: qsTr("Состав плейлиста")
                font.bold: true
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 170
                border.color: "#bbbbbb"
                radius: 3
                ListView {
                    id: itemsView
                    anchors.fill: parent
                    anchors.margins: 1
                    clip: true
                    model: playlistItems
                    // Сам delegate — DropArea: вложенные MouseArea/Image —
                    // его дети, поэтому drop доставляется в этот элемент.
                    delegate: DropArea {
                        width: itemsView.width
                        height: 30
                        onDropped: {
                            var data = drop.getDataAsString(
                                           "application/x-pyplayer-item")
                            if (data !== "") {
                                var from = Number(data)
                                if (!isNaN(from))
                                    playlistItems.moveItem(from, model.position)
                            }
                        }
                        Rectangle {
                            anchors.fill: parent
                            color: itemMouse.containsMouse ? "#eeeeee" : "white"
                            border.color: "#eeeeee"
                        }
                        Drag.active: itemMouse.drag.active
                        Drag.hotSpot.x: width / 2
                        Drag.hotSpot.y: height / 2
                        Drag.mimeData: ({
                            "application/x-pyplayer-item": String(model.position)
                        })
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 3
                            Text {
                                text: (model.refType === "track" ? "♪ " : "▸ ")
                                      + model.title
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Text {
                                text: model.artist
                                font.pixelSize: 11
                                color: "#777777"
                                elide: Text.ElideRight
                            }
                        }
                        MouseArea {
                            id: itemMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            drag.target: parent
                            drag.axis: Drag.XAndYAxis
                            onClicked: {
                                if (mouse.button === Qt.RightButton)
                                    itemMenu.popup()
                            }
                        }
                        Menu {
                            id: itemMenu
                            MenuItem {
                                text: qsTr("Воспроизвести")
                                enabled: model.refType === "track"
                                onTriggered: player.play(model.refId)
                            }
                            MenuItem {
                                text: qsTr("Удалить из плейлиста")
                                onTriggered: playlistItems.removeAt(model.position)
                            }
                        }
                    }
                }
            }
            Text {
                id: statsText
                text: qsTr("Выберите плейлист, чтобы увидеть статистику")
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        // ---------- Колонка 2: музыкальные файлы ----------
        ColumnLayout {
            Layout.preferredWidth: 380
            Layout.fillHeight: true
            spacing: 4

            Label {
                text: qsTr("Музыкальные файлы")
                font.bold: true
                font.pixelSize: 16
            }
            Label {
                text: qsTr("Папка: %1").arg(musicDir || "—")
                font.pixelSize: 11
                color: "#666666"
                elide: Text.ElideMiddle
                Layout.fillWidth: true
            }
            TextField {
                id: searchField
                placeholderText: qsTr("Поиск по названию или исполнителю")
                Layout.fillWidth: true
                onTextChanged: trackFilter.setFilterText(text)
            }
            Label {
                objectName: "foundLabel"
                text: qsTr("Найдено: %1").arg(trackFilter.count())
                font.pixelSize: 11
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                border.color: "#bbbbbb"
                radius: 3
                ListView {
                    id: tracksView
                    anchors.fill: parent
                    anchors.margins: 1
                    clip: true
                    model: trackFilter
                    delegate: Rectangle {
                        width: tracksView.width
                        height: 52
                        color: trackMouse.containsMouse ? "#eef5ff" : "white"
                        border.color: "#eeeeee"
                        Drag.active: trackMouse.drag.active
                        Drag.hotSpot.x: width / 2
                        Drag.hotSpot.y: height / 2
                        Drag.mimeData: ({
                            "application/x-pyplayer-track": String(model.id)
                        })
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 4
                            spacing: 0
                            Text {
                                text: model.title
                                font.bold: true
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Text {
                                text: qsTr("%1 · %2 · %3 · %4 Гц").arg(
                                          model.artist || "—").arg(
                                          root.formatTime(model.duration)).arg(
                                          model.format).arg(model.sampleRate)
                                font.pixelSize: 11
                                color: "#666666"
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                        MouseArea {
                            id: trackMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            drag.target: parent
                            drag.axis: Drag.XAndYAxis
                            onClicked: {
                                if (mouse.button === Qt.RightButton)
                                    trackMenu.popup()
                            }
                            onDoubleClicked: player.play(model.id)
                        }
                        Menu {
                            id: trackMenu
                            MenuItem {
                                text: qsTr("Воспроизвести")
                                onTriggered: player.play(model.id)
                            }
                            MenuItem {
                                text: qsTr("Добавить в выбранный плейлист")
                                onTriggered: {
                                    if (root.selectedPlaylist > 0)
                                        playlistItems.addTrack(model.id)
                                }
                            }
                            MenuItem {
                                text: qsTr("Свойства")
                                onTriggered: {
                                    root.propsTitle = model.title
                                    root.propsDetails = qsTr(
                                        "Исполнитель: %1\nФайл: %2\n"
                                        + "Длительность: %3\nФормат: %4\n"
                                        + "Частота: %5 Гц").arg(
                                        model.artist || "—").arg(model.path).arg(
                                        root.formatTime(model.duration)).arg(
                                        model.format).arg(model.sampleRate)
                                    propsPopup.open()
                                }
                            }
                        }
                    }
                }
            }
        }

        // ---------- Колонка 3: проигрывание ----------
        ColumnLayout {
            Layout.preferredWidth: 360
            Layout.fillHeight: true
            spacing: 6

            Label {
                text: qsTr("Проигрывание")
                font.bold: true
                font.pixelSize: 16
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                border.color: "#dddddd"
                radius: 3
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 4
                    spacing: 6
                    TrackIcon { glyph: "⏮" }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Text {
                            text: qsTr("Предыдущий трек")
                            font.pixelSize: 10
                            color: "#888888"
                        }
                        Text {
                            id: prevLabel
                            text: "—"
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                border.color: "#bbbbbb"
                radius: 3
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        TrackIcon { glyph: "♪" }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                id: currentLabel
                                text: root.currentTitle || qsTr("Трек не выбран")
                                font.bold: true
                                font.pixelSize: 15
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            Text {
                                id: currentDetails
                                text: ""
                                font.pixelSize: 11
                                color: "#666666"
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            Text {
                                text: root.currentPlaylistName
                                      ? qsTr("Плейлист: %1").arg(
                                            root.currentPlaylistName)
                                      : ""
                                font.pixelSize: 11
                                color: "#666666"
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Button {
                            text: "⏮"
                            ToolTip.visible: hovered
                            ToolTip.text: qsTr("Предыдущий")
                            onClicked: player.prev_track()
                        }
                        Button {
                            id: playButton
                            text: "▶"
                            Layout.preferredWidth: 56
                            onClicked: player.toggle_play()
                        }
                        Button {
                            text: "⏭"
                            ToolTip.visible: hovered
                            ToolTip.text: qsTr("Следующий")
                            onClicked: player.next_track()
                        }
                        Item { Layout.fillWidth: true }
                        Button {
                            text: "🔀"
                            ToolTip.visible: hovered
                            ToolTip.text: qsTr("Перемешать")
                            onClicked: {
                                var next = ({
                                    sequential: "random_no_repeat",
                                    sequential_loop: "random_loop_no_repeat",
                                    random_no_repeat: "random_repeat",
                                    random_repeat: "sequential",
                                    random_loop_no_repeat: "sequential_loop"
                                })[root.currentMode] || "sequential"
                                root.applyMode(next)
                            }
                        }
                        Button {
                            text: "🔁"
                            ToolTip.visible: hovered
                            ToolTip.text: qsTr("Повтор")
                            onClicked: {
                                var looping = root.currentMode === "sequential_loop"
                                              || root.currentMode === "random_loop_no_repeat"
                                var next = looping
                                        ? (root.currentMode === "sequential_loop"
                                           ? "sequential" : "random_no_repeat")
                                        : (root.currentMode === "sequential"
                                           ? "sequential_loop" : "random_loop_no_repeat")
                                root.applyMode(next)
                            }
                        }
                    }

                    Slider {
                        id: seekSlider
                        Layout.fillWidth: true
                        from: 0
                        to: 1
                        value: 0
                        onMoved: player.seek(value)
                    }
                    Label {
                        id: timeLabel
                        text: "0:00 / 0:00"
                        font.pixelSize: 11
                        Layout.alignment: Qt.AlignHCenter
                    }
                    Label {
                        text: qsTr("Режим: %1").arg(root.currentMode)
                        font.pixelSize: 11
                        color: "#666666"
                        Layout.alignment: Qt.AlignHCenter
                    }
                    Item { Layout.fillHeight: true }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: qsTr("Громкость") }
                        Slider {
                            from: 0
                            to: 1
                            value: 1
                            Layout.fillWidth: true
                            onMoved: player.set_volume(value)
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                border.color: "#dddddd"
                radius: 3
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 4
                    spacing: 6
                    TrackIcon { glyph: "⏭" }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Text {
                            text: qsTr("Следующий трек")
                            font.pixelSize: 10
                            color: "#888888"
                        }
                        Text {
                            id: nextLabel
                            text: "—"
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }
    }

    // ---------- Сигналы контроллера (только через QML-слой) ----------
    Connections {
        target: player
        function onCurrentTrackChanged(track) {
            root.currentTitle = track && track.title ? track.title : ""
            root.refreshTrackLabels()  // подписи + детали (исполнитель/формат)
            playButton.text = player.is_playing() ? "⏸" : "▶"
        }
        function onPlaylistChanged(playlistId, name) {
            root.currentPlaylistName = name
            root.refreshTrackLabels()
        }
        function onPositionChanged(position, duration) {
            if (!seekSlider.pressed) {
                seekSlider.to = duration > 0 ? duration : 1
                seekSlider.value = position
                timeLabel.text = root.formatTime(position) + " / "
                                 + root.formatTime(duration)
            }
            playButton.text = player.is_playing() ? "⏸" : "▶"
        }
    }
    Connections {
        target: playlistModel
        function onErrorOccurred(message) { root.statusText = message }
    }
    Connections {
        target: playlistItems
        function onErrorOccurred(message) { root.statusText = message }
        function onItemsChanged() {
            root.refreshTrackLabels()
            playlistModel.reload()  // счётчик треков в колонке 1
            if (root.selectedPlaylist > 0)
                statsText.text = root.formatStats(
                    playlistModel.stats(root.selectedPlaylist))
        }
    }

    // ---------- Диалоги ----------
    Popup {
        id: createPopup
        anchors.centerIn: parent
        width: 360
        height: 170
        modal: true
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            TextField {
                id: createName
                placeholderText: qsTr("Название плейлиста")
                Layout.fillWidth: true
            }
            TextField {
                id: createDescription
                placeholderText: qsTr("Описание")
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Button {
                    text: qsTr("Отмена")
                    onClicked: createPopup.close()
                }
                Button {
                    text: qsTr("Создать")
                    onClicked: {
                        playlistModel.create(createName.text,
                                             createDescription.text)
                        createName.text = ""
                        createDescription.text = ""
                        createPopup.close()
                    }
                }
            }
        }
    }

    Popup {
        id: renamePopup
        anchors.centerIn: parent
        width: 340
        height: 130
        modal: true
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            TextField {
                id: renameField
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Button {
                    text: qsTr("Отмена")
                    onClicked: renamePopup.close()
                }
                Button {
                    text: qsTr("Сохранить")
                    onClicked: {
                        playlistModel.rename(root.selectedPlaylist,
                                             renameField.text)
                        renamePopup.close()
                    }
                }
            }
        }
    }

    Popup {
        id: propsPopup
        anchors.centerIn: parent
        width: 420
        height: 230
        modal: true
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            Label {
                text: root.propsTitle
                font.bold: true
                font.pixelSize: 15
            }
            Text {
                text: root.propsDetails
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Button {
                text: qsTr("Закрыть")
                Layout.alignment: Qt.AlignRight
                onClicked: propsPopup.close()
            }
        }
    }

    Popup {
        id: settingsPopup
        anchors.centerIn: parent
        width: 380
        height: 190
        modal: true
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            Label {
                text: qsTr("Температура случайного выбора: %1").arg(
                          temperatureSlider.value.toFixed(2))
            }
            Slider {
                id: temperatureSlider
                from: 0.1
                to: 2.0
                value: settings.temperature
                Layout.fillWidth: true
                onMoved: settings.setTemperature(value)
            }
            Label {
                text: qsTr("Максимальная глубина плейлиста: %1").arg(
                          settings.maxPlaylistDepth())
                font.pixelSize: 11
                color: "#666666"
            }
            Button {
                text: qsTr("Закрыть")
                Layout.alignment: Qt.AlignRight
                onClicked: settingsPopup.close()
            }
        }
    }
}
