import sys
from qtpy.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QPushButton, QWidget, QTreeWidget, QTreeWidgetItem, QFileDialog, QHBoxLayout, QLabel, QSlider
from qtpy.QtCore import Qt, QUrl
from qtpy.QtMultimediaWidgets import QVideoWidget
from qtpy.QtMultimedia import QMediaPlayer, QMediaContent

class IPTVPlayer(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("IPTV Player")
        self.setGeometry(100, 100, 1000, 600)

        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)

        self.layout = QVBoxLayout()

        # Górna część: Video i Playlist obok siebie
        upper_layout = QHBoxLayout()

        # Video Player
        self.video_widget = QVideoWidget()
        upper_layout.addWidget(self.video_widget)

        # Playlist
        self.playlist_tree = QTreeWidget()
        self.playlist_tree.setColumnCount(1)
        self.playlist_tree.setHeaderLabels(["Name"])
        self.playlist_tree.itemDoubleClicked.connect(self.play_channel_double_click)
        self.playlist_tree.setMinimumWidth(int(self.width() * 0.2))
        #self.playlist_tree.setMinimumHeight(int(self.height() * 0.2))
        self.playlist_tree.setMaximumWidth(int(self.width() * 0.2))
        #self.playlist_tree.setMaximumHeight(int(self.height() * 0.2))
        upper_layout.addWidget(self.playlist_tree)

        self.layout.addLayout(upper_layout)

        # Kontrole odtwarzania
        control_layout = QHBoxLayout()

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.play_channel)
        control_layout.addWidget(self.play_button)

        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self.pause_channel)
        control_layout.addWidget(self.pause_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_channel)
        control_layout.addWidget(self.stop_button)

        self.mute_button = QPushButton("Mute")
        self.mute_button.clicked.connect(self.toggle_mute)
        control_layout.addWidget(self.mute_button)

        # Przyciski regulacji głośności
        self.volume_down_button = QPushButton("-")
        self.volume_down_button.clicked.connect(self.volume_down)
        control_layout.addWidget(self.volume_down_button)

        self.volume_label = QLabel("Volume:")
        control_layout.addWidget(self.volume_label)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setMaximum(100)
        self.volume_slider.setValue(50)
        self.volume_slider.valueChanged.connect(self.set_volume)
        control_layout.addWidget(self.volume_slider)

        self.volume_up_button = QPushButton("+")
        self.volume_up_button.clicked.connect(self.volume_up)
        control_layout.addWidget(self.volume_up_button)

        self.layout.addLayout(control_layout)

        # Przycisk "Load Playlist"
        load_playlist_button = QPushButton("Load Playlist")
        load_playlist_button.clicked.connect(self.load_playlist)
        self.layout.addWidget(load_playlist_button)

        self.central_widget.setLayout(self.layout)

        # Inicjalizacja odtwarzacza
        self.media_player = QMediaPlayer()
        self.media_player.setVideoOutput(self.video_widget)

    def play_channel(self):
        current_item = self.playlist_tree.currentItem()
        if current_item and current_item.childCount() == 0:
            channel_url = current_item.data(0, Qt.UserRole)
            if channel_url:
                self.media_player.setMedia(QMediaContent(QUrl.fromUserInput(channel_url)))
                self.media_player.play()

    def pause_channel(self):
        # Aby rozpocząć buforowanie, ustaw ponownie aktualną treść medialną
        current_media = self.media_player.media()
        if current_media:
            self.media_player.setMedia(current_media)
            self.media_player.pause()

    def stop_channel(self):
        self.media_player.stop()

    def toggle_mute(self):
        self.media_player.setMuted(not self.media_player.isMuted())

    def set_volume(self):
        volume = self.volume_slider.value()
        self.media_player.setVolume(volume)

    def volume_up(self):
        current_volume = self.media_player.volume()
        new_volume = min(current_volume + 10, 100)
        self.media_player.setVolume(new_volume)
        self.volume_slider.setValue(new_volume)

    def volume_down(self):
        current_volume = self.media_player.volume()
        new_volume = max(current_volume - 10, 0)
        self.media_player.setVolume(new_volume)
        self.volume_slider.setValue(new_volume)

    def play_channel_double_click(self, item):
        if item and item.childCount() == 0:
            channel_url = item.data(0, Qt.UserRole)
            if channel_url:
                self.media_player.setMedia(QMediaContent(QUrl.fromUserInput(channel_url)))
                self.media_player.play()

    def load_playlist(self):
        file_dialog = QFileDialog()
        file_dialog.setNameFilter("M3U Playlist (*.m3u *.m3u8)")
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)

        if file_dialog.exec():
            file_path = file_dialog.selectedFiles()[0]
            self.parse_playlist(file_path)

    def parse_playlist(self, file_path):
        with open(file_path, 'r') as file:
            playlist_data = file.readlines()

        self.playlist_tree.clear()

        current_group = None

        for line in playlist_data:
            line = line.strip()
            if line.startswith('#EXTGRP:'):
                group_name = line.split(':')[-1]
                if current_group is None or current_group.text(0) != group_name:
                    current_group = QTreeWidgetItem([group_name])
                    self.playlist_tree.addTopLevelItem(current_group)
            elif line.startswith('#EXTINF:'):
                if current_group is not None:
                    channel_name = line.split(',')[-1]
                    channel_item = QTreeWidgetItem([channel_name])
                    current_group.addChild(channel_item)
            elif line.startswith('http') or line.startswith('https'):
                if current_group is not None and current_group.childCount() > 0:
                    channel_item = current_group.child(current_group.childCount() - 1)
                    channel_item.setData(0, Qt.UserRole, line)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    player = IPTVPlayer()
    player.show()
    sys.exit(app.exec())
