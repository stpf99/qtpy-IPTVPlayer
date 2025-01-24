import sys
import os
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlink
import numpy as np
import sounddevice as sd
from piper import PiperVoice
from threading import Thread, Lock
from queue import Queue
import time
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
    QLineEdit,
    QHeaderView,
    QPlainTextEdit
)
from qtpy.QtCore import Qt, QUrl, QTimer, QThread, Signal
from qtpy.QtMultimediaWidgets import QVideoWidget
from qtpy.QtMultimedia import QMediaPlayer, QMediaContent
from whisper_live.client import TranscriptionClient
import locale
from deep_translator import GoogleTranslator
from io import StringIO
import threading
import subprocess
import queue

class TTSHandler:
    def __init__(self, model_path=None, config_path=None):
        self.audio_queue = queue.Queue()
        self.is_playing = False
        self.process = None
        self.running = True
        self.stream = None
        self.sample_rate = 22050  # Piper's default sample rate
        self.model_path = model_path
        self.config_path = config_path
        
        # Sprawdź dostępność TTS
        self.tts_available = False
        if model_path and os.path.exists(model_path):
            if config_path is None or os.path.exists(config_path):
                self.tts_available = True
        
    def start_audio_stream(self):
        """Initialize and start the audio output stream"""
        def audio_callback(outdata, frames, time, status):
            if status:
                print(f"Audio stream status: {status}")
            try:
                data = self.audio_queue.get_nowait()
                if len(data) < frames:
                    padding = np.zeros(frames - len(data), dtype=np.float32)
                    data = np.concatenate([data, padding])
                outdata[:, 0] = data[:frames]
            except queue.Empty:
                outdata.fill(0)  # Output silence when queue is empty

        if not self.is_playing:
            self.stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype=np.float32,
                callback=audio_callback,
                blocksize=1024
            )
            self.stream.start()
            self.is_playing = True

    def stop_audio_stream(self):
        """Stop the audio stream and cleanup"""
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        self.is_playing = False
        if self.process:
            self.process.terminate()
            self.process = None

    def speak(self, text):
        """Non-blocking method to speak text using piper-tts"""
        if not self.tts_available:
            print("TTS is not available - model files not found")
            return

        def stream_audio():
            try:
                # Zamknij istniejący proces, jeśli istnieje
                if self.process is not None:
                    try:
                        self.process.terminate()
                        self.process.wait()
                    except Exception:
                        pass

                cmd = ["piper-tts", "--output-raw"]
                if self.model_path:
                    cmd.extend(["-m", self.model_path])
                if self.config_path:
                    cmd.extend(["-c", self.config_path])
                    
                self.process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0
                )

                # Wyczyść kolejkę przed dodaniem nowych danych
                while not self.audio_queue.empty():
                    try:
                        self.audio_queue.get_nowait()
                    except queue.Empty:
                        break

                # Wyślij tekst do piper-tts
                self.process.stdin.write(text.encode('utf-8') + b'\n')
                self.process.stdin.flush()

                # Odczytaj i dodaj dane audio do kolejki
                chunk_size = 2048
                while self.running:
                    audio_chunk = self.process.stdout.read(chunk_size)
                    if not audio_chunk:
                        break
                    # Konwersja z 16-bit PCM do float32
                    audio_data = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float32) / 32768.0
                    self.audio_queue.put(audio_data)

            except Exception as e:
                print(f"Error in TTS streaming: {e}")
            finally:
                # Upewnij się, że proces jest zamknięty
                if self.process is not None:
                    try:
                        self.process.terminate()
                        self.process.wait()
                    except Exception:
                        pass

        # Uruchom w osobnym wątku z daemon=True
        threading.Thread(target=stream_audio, daemon=True).start()

        # Włącz strumień audio, jeśli nie jest aktywny
        if not self.is_playing:
            self.start_audio_stream()

class OutputRedirector:
    def __init__(self, queue):
        self.queue = queue

    def write(self, text):
        self.queue.put(text)

    def flush(self):
        pass

def translate_text(text, target_lang):
    try:
        return GoogleTranslator(target=target_lang).translate(text)
    except Exception as e:
        print(f"Translation error: {e}")
        return text

class IPTVPlayer(QMainWindow):
    subtitle_signal = Signal(str)
    whisper_output = Signal(str)

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
        self.url_field.setText("https://iptv-org.github.io/iptv/categories/music.m3u")
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
        self.playlist_tree.setHeaderLabels(["Channel Name"])
        self.playlist_tree.itemDoubleClicked.connect(self.play_channel_double_click)
        self.playlist_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        upper_layout.addWidget(self.playlist_tree)

        self.layout.addLayout(upper_layout)

        # Subtitle Display
        self.subtitle_box = QPlainTextEdit()
        self.subtitle_box.setReadOnly(True)
        self.layout.addWidget(self.subtitle_box)

        # Connect subtitle signal
        self.subtitle_signal.connect(self.update_subtitles_gui)

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


        # Dodaj przycisk TTS do interfejsu
        self.tts_button = QPushButton("TTS: Wyłączony")
        self.tts_button.clicked.connect(self.toggle_tts)
        control_layout.addWidget(self.tts_button)

        self.layout.addLayout(control_layout)

        # Exit button
        exit_button = QPushButton("Exit")
        exit_button.clicked.connect(self.close_application)
        self.layout.addWidget(exit_button)

        self.central_widget.setLayout(self.layout)

        # Initialize player
        self.media_player = QMediaPlayer()
        self.media_player.setVideoOutput(self.video_widget)

        # Subtitle client
        self.transcription_client = None
        
        self.is_fullscreen = False
        self.auto_hide_timer = QTimer()
        self.auto_hide_timer.timeout.connect(self.hide_playlist)

        self.load_last_playlist()

       # Get system locale for translation
        try:
            self.system_locale = locale.getlocale()[0].split('_')[0]  # Gets language code (e.g., 'pl' from 'pl_PL')
        except:
            self.system_locale = 'pl'  # Default to Polish if locale detection fails
            
        # Initialize translator
        self.translator = GoogleTranslator(target=self.system_locale)

        self.whisper_queue = queue.Queue()
        
        # Timer do sprawdzania kolejki
        self.whisper_timer = QTimer()
        self.whisper_timer.timeout.connect(self.check_whisper_output)
        self.whisper_timer.start(100)  # sprawdzaj co 100ms

        # Połącz sygnał z metodą aktualizacji GUI
        self.whisper_output.connect(self.update_subtitles_gui)

        # W metodzie __init__ klasy IPTVPlayer:
        try:
            model_path = "pl_PL-darkman-medium.onnx"
            config_path = "pl_pl_PL_darkman_medium_pl_PL-darkman-medium.onnx.json"
            self.tts_handler = TTSHandler(model_path=model_path, config_path=config_path)
            self.tts_enabled = False
        except Exception as e:
            print(f"Błąd inicjalizacji TTS: {e}")
            self.tts_handler = None

    def initialize_piper_tts(self):
        """Initialize Piper TTS with Polish voice model"""
        try:
            model_path = "/home/ts/qtpy-IPTVPlayer-main/pl_PL-darkman-medium.onnx"
            config_path = "/home/ts/qtpy-IPTVPlayer-main/pl_pl_PL_darkman_medium_pl_PL-darkman-medium.onnx.json"
            
            if not os.path.exists(model_path) or not os.path.exists(config_path):
                raise FileNotFoundError("Piper TTS model files not found")
                
            self.piper_voice = PiperVoice.load(model_path, config_path)
            self.audio_queue = []
            self.sample_rate = 22050  # Default Piper sample rate
            
        except Exception as e:
            print(f"Error initializing Piper TTS: {e}")
            self.piper_voice = None

    def play_channel_double_click(self, item):
        if item and item.childCount() == 0:
            channel_url = item.data(0, Qt.UserRole)
            if channel_url:
                self.media_player.setMedia(QMediaContent(QUrl.fromUserInput(channel_url)))
                self.media_player.play()
                self.start_subtitles(channel_url)

    def start_subtitles(self, hls_url):
        if self.transcription_client:
            self.transcription_client = None

        try:
            self.transcription_client = TranscriptionClient(
                "localhost", 9090, lang="en"
            )
        
            def process_subtitles():
                # Zapisz oryginalny stdout
                old_stdout = sys.stdout
            
                try:
                    # Przekieruj stdout do naszego OutputRedirector
                    sys.stdout = OutputRedirector(self.whisper_queue)
                
                    # Sprawdź, czy klient został poprawnie zainicjalizowany
                    if not self.transcription_client:
                        raise Exception("TranscriptionClient not initialized")

                    # Próba uruchomienia transkrypcji
                    try:
                        # Bezpośrednie wywołanie metody process_stream
                        self.transcription_client.process_stream(hls_url)
                    except AttributeError:
                        # Jeśli process_stream nie jest dostępne, spróbuj standardowego wywołania
                        result = self.transcription_client(hls_url=hls_url)
                        if result:
                            for transcript in result:
                                print(transcript['text'])
                    
                except Exception as e:
                    print(f"Transcription error: {str(e)}")
                finally:
                    # Przywróć oryginalny stdout
                    sys.stdout = old_stdout

            # Uruchom przetwarzanie napisów w osobnym wątku
            Thread(target=process_subtitles, daemon=True).start()

        except Exception as e:
            self.whisper_output.emit(f"Error initializing transcription client: {e}")
            print(f"Error initializing transcription client: {e}")
            
    def check_whisper_output(self):
        # Use a class-level set to track spoken translations across method calls
        if not hasattr(self, '_spoken_translations'):
            self._spoken_translations = set()

        try:
            while True:
                try:
                    text = self.whisper_queue.get_nowait()
                    if text and text.strip():
                        translated_text = self.translator.translate(text.strip())
                        combined_text = f"Original: {text.strip()}\n{self.system_locale.upper()}: {translated_text}"
                
                        # Aktualizuj GUI
                        self.whisper_output.emit(combined_text)
                
                        # Synteza TTS tylko dla unikalnych, nieprzetworzonych wcześniej tłumaczeń
                        if (self.tts_enabled and 
                            self.tts_handler and 
                            self.tts_handler.tts_available and 
                            translated_text not in self._spoken_translations):
                        
                            QTimer.singleShot(50, lambda t=translated_text: 
                                self.tts_handler.speak(t))
                        
                            # Dodaj do zbioru już przetłumaczonych i wypowiedzianych tekstów
                            self._spoken_translations.add(translated_text)
                        
                            # Opcjonalnie: ogranicz rozmiar zbioru, aby nie rosło w nieskończoność
                            if len(self._spoken_translations) > 100:
                                self._spoken_translations.clear()
        
                except queue.Empty:
                    break
                
        except Exception as e:
            print(f"Error in check_whisper_output: {e}")
    
    def update_subtitles_gui(self, text):
        """Aktualizuje napisy w GUI"""
        current_text = self.subtitle_box.toPlainText()
        # Dodaj nowy tekst na końcu, zachowując historię
        if current_text:
            new_text = current_text + "\n" + text
        else:
            new_text = text
            
        self.subtitle_box.setPlainText(new_text)
        
        # Przewiń do końca
        cursor = self.subtitle_box.textCursor()
        cursor.movePosition(cursor.End)
        self.subtitle_box.setTextCursor(cursor)
        self.subtitle_box.ensureCursorVisible()

    def speak_text(self, text):
        """Send text to TTS handler for synthesis"""
        if self.tts_enabled and self.tts_handler.running:
            Thread(target=self.tts_handler.synthesize_speech, args=(text,), daemon=True).start()

    def update_subtitles_gui(self, text):
        """Update subtitles in GUI thread"""
        self.subtitle_box.setPlainText(text)
        # Auto-scroll to bottom
        self.subtitle_box.verticalScrollBar().setValue(
            self.subtitle_box.verticalScrollBar().maximum()
        )

    def toggle_tts(self):
        self.tts_enabled = not self.tts_enabled
        self.tts_button.setText(f"TTS: {'Włączony' if self.tts_enabled else 'Wyłączony'}")
    
        if self.tts_enabled:
            self.tts_handler.start_audio_stream()
        else:
            self.tts_handler.stop_audio_stream()
    
    def process_text_for_tts(self, text):
        if self.tts_enabled:
            self.tts_handler.speak(text)

    def stop_channel(self):
        self.media_player.stop()
        if self.transcription_client:
            self.transcription_client = None

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

    def toggle_mute(self):
        self.media_player.setMuted(not self.media_player.isMuted())
        self.mute_button.setText("Unmute" if self.media_player.isMuted() else "Mute")

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
            self.hide_playlist()
        elif event.button() == Qt.LeftButton and self.isFullScreen():
            self.showNormal()
            self.auto_hide_timer.stop()
            self.is_fullscreen = False
            self.show_playlist()

        self.adjust_playlist_width()

    def adjust_playlist_width(self):
        if self.is_fullscreen:
            self.playlist_tree.setFixedWidth(int(self.width() * 0.2))
        else:
            self.playlist_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
            self.playlist_tree.setMinimumWidth(200)
            self.playlist_tree.setMaximumWidth(400)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.adjust_playlist_width()

    def hide_playlist(self):
        if self.is_fullscreen:
            self.playlist_tree.hide()

    def show_playlist(self):
        if not self.is_fullscreen:
            self.playlist_tree.show()

    def mouseMoveEvent(self, event):
        if self.is_fullscreen:
            self.show_playlist()
            self.auto_hide_timer.start(5000)
        super().mouseMoveEvent(event)

    def closeEvent(self, event):
        """Czyszczenie zasobów przed zamknięciem"""
        if self.tts_handler:
            self.tts_handler.stop_audio_stream()
        super().closeEvent(event)
        
if __name__ == "__main__":
    app = QApplication(sys.argv)
    player = IPTVPlayer()
    player.show()
    sys.exit(app.exec())
                            
