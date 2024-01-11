import sys
from qtpy.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QPushButton, QWidget, QTreeWidget, QTreeWidgetItem, QFileDialog, QHBoxLayout, QLabel, QSlider, QMessageBox
from qtpy.QtCore import Qt, QUrl, QTimer
from qtpy.QtMultimediaWidgets import QVideoWidget
from qtpy.QtMultimedia import QMediaPlayer, QMediaContent
import m3u8

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
        self.video_widget.setMouseTracking(True)
        self.video_widget.mouseDoubleClickEvent = self.toggle_fullscreen
        upper_layout.addWidget(self.video_widget)

        # Playlist
        self.playlist_tree = QTreeWidget()
        self.playlist_tree.setColumnCount(1)
        self.playlist_tree.setHeaderLabels(["Name"])
        self.playlist_tree.itemDoubleClicked.connect(self.play_channel_double_click)
        self.playlist_tree.setMinimumWidth(int(self.width() * 0.2))
        self.playlist_tree.setMaximumWidth(int(self.width() * 0.2))
        upper_layout.addWidget(self.playlist_tree)

        self.layout.addLayout(upper_layout)

        # Kontrole odtwarzania
        control_layout = QHBoxLayout()

        self.play_pause_button = QPushButton("Play")
        self.play_pause_button.clicked.connect(self.toggle_play_pause)
        control_layout.addWidget(self.play_pause_button)

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

        # Timer dla autoukrywania playlisty tylko w trybie pełnoekranowym
        self.auto_hide_timer = QTimer(self)
        self.auto_hide_timer.timeout.connect(self.hide_playlist)

        self.is_fullscreen = False

    def toggle_play_pause(self):
        if self.media_player.state() == QMediaPlayer.PlayingState:
            self.media_player.pause()
            self.play_pause_button.setText("Play")
        else:
            self.media_player.play()
            self.play_pause_button.setText("Pause")

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
                self.show_playlist()

    def load_playlist(self):
        file_dialog = QFileDialog()
        file_dialog.setNameFilter("M3U Playlist (*.m3u *.m3u8)")
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)

        if file_dialog.exec():
            file_path = file_dialog.selectedFiles()[0]
            self.parse_playlist(file_path)

    def parse_playlist(self, file_path):
        print(f"Loading playlist from: {file_path}")

        try:
            with open(file_path, 'r') as file:
                playlist_data = file.readlines()

            for i, line in enumerate(playlist_data):
                line = line.strip()

                if line.startswith('#EXTINF:'):
                    try:
                        extinf_info = line.split(',')[1]
                        channel_name = extinf_info.split('group-title="')[-1].split('"')[0].strip()
                        tvg_id = extinf_info.split('tvg-id="')[-1].split('"')[0].strip()

                        channel_item = QTreeWidgetItem([channel_name])
                        channel_item.setData(0, Qt.UserRole, playlist_data[i + 1].strip())

                        m3u8_links = self.extract_m3u8_links(playlist_data[i + 1:])
                        for m3u8_link in m3u8_links:
                            sub_item = QTreeWidgetItem([m3u8_link])
                            sub_item.setData(0, Qt.UserRole, m3u8_link)
                            channel_item.addChild(sub_item)

                        self.playlist_tree.addTopLevelItem(channel_item)

                    except Exception as e:
                        self.show_error_message(f"Error parsing EXTINF line ({i + 1}): {e}")

        except Exception as e:
            self.show_error_message(f"Error loading playlist: {e}")

    def extract_m3u8_links(self, lines):
        m3u8_links = []
        for line in lines:
            line = line.strip()
            if line.startswith('http') or line.startswith('https'):
                m3u8_links.append(line)
            else:
                break
        return m3u8_links

    def show_error_message(self, message):
        QMessageBox.critical(self, "Error", message)

    def toggle_fullscreen(self, event):
        if event.button() == Qt.LeftButton and not self.isFullScreen():
            self.showFullScreen()
            self.auto_hide_timer.start(5000)  # 5000 milisekund (5 sekundy)
            self.is_fullscreen = True
        elif event.button() == Qt.LeftButton and self.isFullScreen():
            self.showNormal()
            self.auto_hide_timer.stop()
            self.is_fullscreen = False
            self.playlist_tree.show()

    def hide_playlist(self):
        if self.is_fullscreen:
            self.playlist_tree.hide()

    def show_playlist(self):
        if self.is_fullscreen:
            self.playlist_tree.show()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    player = IPTVPlayer()
    player.show()
    sys.exit(app.exec())

