import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    visible: true
    width: 1000
    height: 640
    minimumWidth: 800
    minimumHeight: 480
    title: qsTr("PyPlayer")

    RowLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 8

        // Колонка 1: плейлисты и треки
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 4

            Label {
                text: qsTr("Плейлисты и треки")
                font.bold: true
                font.pixelSize: 16
            }
        }

        // Колонка 2: музыкальные файлы
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 4

            Label {
                text: qsTr("Музыкальные файлы")
                font.bold: true
                font.pixelSize: 16
            }
        }

        // Колонка 3: проигрывание
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 4

            Label {
                text: qsTr("Проигрывание")
                font.bold: true
                font.pixelSize: 16
            }
        }
    }
}
