import sys
import os
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlink
from qtpy.QtWidgets import (
    QApplication, 
    QMainWindow, 
    QVBoxLayout, 
    QPushButton, 
    QWidget, 
    QTreeWidget, 
    QTreeWidgetItem, 
    QFileDialog, 
    QHBoxLayout, 
    QLabel, 
    QSlider, 
    QMessageBox, 
    QProgressDialog, 
    QLineEdit
)
from qtpy.QtCore import Qt, QUrl, QTimer
from qtpy.QtMultimediaWidgets import QVideoWidget
from qtpy.QtMultimedia import QMediaPlayer, QMediaContent

DEFAULT_PLAYLIST_URL = "https://iptv-org.github.io/iptv/categories/music.m3u"

class IPTVPlayer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
        self.last_playlist = None
        self.active_streams = []
        self.group_items = {}

        self.setWindowTitle("IPTV Player")
        self.setGeometry(100, 100, 1000, 600)

        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)

        self.layout = QVBoxLayout()

        # Field for remote playlist URL
        url_layout = QHBoxLayout()
        self.url_field = QLineEdit()
        self.url_field.setPlaceholderText("Enter remote playlist URL")
        self.url_field.setText(DEFAULT_PLAYLIST_URL)
        url_layout.addWidget(QLabel("Remote Playlist URL:"))
        url_layout.addWidget(self.url_field)
        self.layout.addLayout(url_layout)

        # Buttons for playlists
        playlist_buttons_layout = QHBoxLayout()

        load_remote_button = QPushButton("Load Remote Playlist")
        load_remote_button.clicked.connect(self.load_remote_playlist)
        playlist_buttons_layout.addWidget(load_remote_button)

        load_local_button = QPushButton("Load Local Playlist")
        load_local_button.clicked.connect(self.load_playlist)
        playlist_buttons_layout.addWidget(load_local_button)

        self.layout.addLayout(playlist_buttons_layout)

        # Video Player and Playlist
        upper_layout = QHBoxLayout()
        self.video_widget = QVideoWidget()
        self.video_widget.setMouseTracking(True)
        self.video_widget.mouseDoubleClickEvent = self.toggle_fullscreen
        upper_layout.addWidget(self.video_widget)

        self.playlist_tree = QTreeWidget()
        self.playlist_tree.setColumnCount(1)
        self.playlist_tree.setHeaderLabels(["Name"])
        self.playlist_tree.itemDoubleClicked.connect(self.play_channel_double_click)
        upper_layout.addWidget(self.playlist_tree)

        self.layout.addLayout(upper_layout)

        # Playback Controls
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

        # Exit button
        exit_button = QPushButton("Exit")
        exit_button.clicked.connect(self.close_application)
        self.layout.addWidget(exit_button)

        self.central_widget.setLayout(self.layout)

        # Initialize player
        self.media_player = QMediaPlayer()
        self.media_player.setVideoOutput(self.video_widget)

        # Timer for auto-hiding playlist only in fullscreen mode
        self.auto_hide_timer = QTimer(self)
        self.auto_hide_timer.timeout.connect(self.hide_playlist)

        self.is_fullscreen = False

        self.load_last_playlist()

    def load_remote_playlist(self):
        url = self.url_field.text()
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'remote_playlist.m3u')
                with open(local_path, 'w', encoding='utf-8') as f:
                    f.write(response.text)
                self.prompt_check_playlist(local_path)
            else:
                QMessageBox.warning(self, "Error", f"Failed to fetch playlist: {response.status_code}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error fetching playlist: {e}")

    def load_playlist(self):
        file_dialog = QFileDialog()
        file_dialog.setNameFilter("M3U Playlist (*.m3u *.m3u8)")
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)

        if file_dialog.exec():
            file_path = file_dialog.selectedFiles()[0]
            self.prompt_check_playlist(file_path)

    def prompt_check_playlist(self, file_path):
        reply = QMessageBox.question(self, "Check Streams", "Do you want to check the stream availability?", 
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.parse_playlist(file_path, check_streams=True)
        else:
            self.parse_playlist(file_path, check_streams=False)

    def parse_playlist(self, file_path, check_streams):
        self.playlist_tree.clear()
        self.active_streams = []
        self.group_items = {}

        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                playlist_data = file.readlines()

            streams_to_check = []
            current_channel = None

            for line in playlist_data:
                line = line.strip()

                if line.startswith('#EXTINF:'):
                    try:
                        channel_info = {}

                        if 'group-title="' in line:
                            group_title = line.split('group-title="')[1].split('"')[0]
                        else:
                            group_title = "Undefined"

                        if 'tvg-id="' in line:
                            channel_info['tvg-id'] = line.split('tvg-id="')[1].split('"')[0]

                        if 'tvg-logo="' in line:
                            channel_info['tvg-logo'] = line.split('tvg-logo="')[1].split('"')[0]

                        channel_name = line.split(',')[-1].strip()

                        current_channel = {
                            'name': channel_name,
                            'group': group_title,
                            'extinf': line,
                            'info': channel_info
                        }

                    except Exception as e:
                        print(f"Error parsing EXTINF line: {e}")
                        current_channel = None

                elif (line.startswith('http') or line.startswith('https')) and current_channel:
                    current_channel['url'] = line
                    streams_to_check.append(current_channel)

                    if not check_streams:
                        group_name = current_channel['group']
                        if group_name not in self.group_items:
                            group_item = QTreeWidgetItem([group_name])
                            self.playlist_tree.addTopLevelItem(group_item)
                            self.group_items[group_name] = group_item

                        channel_item = QTreeWidgetItem([current_channel['name']])
                        channel_item.setData(0, Qt.UserRole, current_channel['url'])
                        self.group_items[group_name].addChild(channel_item)

                    current_channel = None

            if check_streams:
                progress = QProgressDialog("Checking channel availability...", "Cancel", 0, len(streams_to_check), self)
                progress.setWindowModality(Qt.WindowModal)
                progress.setMinimumDuration(0)
                checked_count = 0

                with ThreadPoolExecutor(max_workers=5) as executor:
                    future_to_stream = {executor.submit(self.check_stream, stream): stream for stream in streams_to_check}

                    for future in as_completed(future_to_stream):
                        checked_count += 1
                        progress.setValue(checked_count)

                        if progress.wasCanceled():
                            executor.shutdown(wait=False)
                            break

                        result = future.result()
                        if result['valid']:
                            stream_info = result['info']
                            self.active_streams.append(stream_info)

                            group_name = stream_info['group']
                            if group_name not in self.group_items:
                                group_item = QTreeWidgetItem([group_name])
                                self.playlist_tree.addTopLevelItem(group_item)
                                self.group_items[group_name] = group_item

                            channel_item = QTreeWidgetItem([stream_info['name']])
                            channel_item.setData(0, Qt.UserRole, stream_info['url'])
                            self.group_items[group_name].addChild(channel_item)

                progress.setValue(len(streams_to_check))

            self.last_playlist = file_path
            self.save_config()
            self.playlist_tree.expandAll()

            if check_streams:
                active_count = len(self.active_streams)
                total_count = len(streams_to_check)
                QMessageBox.information(self, "Summary", f"Found {active_count} active channels out of {total_count} total.")

        except Exception as e:
            self.show_error_message(f"Error loading playlist: {e}")

    def check_stream(self, stream_info):
        url = stream_info['url']
        try:
            session = streamlink.Streamlink()
            session.set_option("stream-timeout", 2)
            session.set_option("hls-timeout", 2)
            session.set_option("http-timeout", 2)
            streams = session.streams(url)

            if streams:
                for quality in ['best', 'worst']:
                    if quality in streams:
                        try:
                            stream = streams[quality]
                            fd = stream.open()
                            fd.close()
                            return {'valid': True, 'info': stream_info}
                        except Exception as e:
                            print(f"Unable to open stream {url} at quality {quality}: {e}")
                            continue

            return {'valid': False, 'info': stream_info}

        except streamlink.StreamlinkError as e:
            print(f"Streamlink error for {url}: {e}")
            return {'valid': False, 'info': stream_info}
        except Exception as e:
            print(f"General error checking stream {url}: {e}")
            return {'valid': False, 'info': stream_info}

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

    def save_config(self):
        config = {'last_playlist': self.last_playlist}
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f)
        except Exception as e:
            print(f"Error saving config: {e}")

    def load_last_playlist(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    last_playlist = config.get('last_playlist')
                    if last_playlist and os.path.exists(last_playlist):
                        self.parse_playlist(last_playlist, check_streams=False)
        except Exception as e:
            print(f"Error loading last playlist: {e}")

    def close_application(self):
        if self.last_playlist:
            reply = QMessageBox.question(self, "Save Playlist", "Do you want to save the current playlist?", 
                                         QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if reply == QMessageBox.Yes:
                self.save_active_playlist()
            elif reply == QMessageBox.Cancel:
                return
        self.close()

    def save_active_playlist(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Playlist", "", "M3U Playlist (*.m3u)")
        if not file_path:
            return

        try:
            with open(file_path, 'w', encoding='utf-8') as file:
                file.write("#EXTM3U\n")

                for i in range(self.playlist_tree.topLevelItemCount()):
                    group_item = self.playlist_tree.topLevelItem(i)

                    for j in range(group_item.childCount()):
                        channel_item = group_item.child(j)
                        name = channel_item.text(0)
                        url = channel_item.data(0, Qt.UserRole)

                        file.write(f'#EXTINF:-1 group-title="{group_item.text(0)}",{name}\n')
                        file.write(f'{url}\n')

            QMessageBox.information(self, "Success", "Playlist saved successfully!")
        except Exception as e:
            self.show_error_message(f"Error saving playlist: {e}")

    def show_error_message(self, message):
        QMessageBox.critical(self, "Error", message)

    def toggle_fullscreen(self, event):
        if event.button() == Qt.LeftButton and not self.isFullScreen():
            self.showFullScreen()
            self.auto_hide_timer.start(5000)
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
