#!/usr/bin/env python3
import http.server
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEAK = str(ROOT / "bin" / "speak")
SETUP = str(ROOT / "bin" / "speak-setup")
TERATTS_PROVIDER = str(ROOT / "providers" / "teratts")

def make_dummy_wav(duration_frames=100, sample_rate=44100):
    # 44-byte standard RIFF/WAVE header followed by 16-bit mono PCM silence
    pcm_bytes = b"\x00\x00" * duration_frames
    data_size = len(pcm_bytes)
    riff_size = data_size + 36
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,  # PCM format chunk size
        1,   # AudioFormat: 1 (PCM)
        1,   # NumChannels: 1 (mono)
        sample_rate,
        sample_rate * 2,  # ByteRate
        2,   # BlockAlign
        16,  # BitsPerSample
        b"data",
        data_size,
    )
    return header + pcm_bytes

class MockTeraHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {
                "status": "ready",
                "app_git_sha": "6432ad33a423703e6bf9059b69f8787315decfce",
                "sample_rate": 44100,
                "voices": ["ru_f1", "ru_f2", "ru_m1", "eng_f3"],
                "queue": {"active": 0, "waiting": 0, "capacity": 15}
            }
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/tts":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8"))
            self.server.requests.append({
                "headers": dict(self.headers),
                "payload": payload,
            })
            if self.server.fail_with_code:
                self.send_response(self.server.fail_with_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "simulated failure"}).encode("utf-8"))
                return

            auth = self.headers.get("Authorization", "")
            if self.server.expected_token and auth != f"Bearer {self.server.expected_token}":
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "unauthorized"}).encode("utf-8"))
                return

            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.end_headers()
            self.wfile.write(make_dummy_wav(duration_frames=200))
        else:
            self.send_response(404)
            self.end_headers()

class TeraTTSProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockTeraHandler)
        cls.port = cls.server.server_port
        cls.server.requests = []
        cls.server.expected_token = "secret-token-123"
        cls.server.fail_with_code = None
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_dir = Path(self.temp_dir.name, "config", "omarchy-tts")
        self.config_dir.mkdir(parents=True)
        self.data_dir = Path(self.temp_dir.name, "data", "omarchy-tts")
        self.data_dir.mkdir(parents=True)
        self.cache_dir = Path(self.temp_dir.name, "cache", "omarchy-tts")
        self.cache_dir.mkdir(parents=True)
        self.run_dir = Path(self.temp_dir.name, "run")
        self.run_dir.mkdir(parents=True)

        self.server.requests.clear()
        self.server.fail_with_code = None
        self.server.expected_token = "secret-token-123"

        self.env = {
            **os.environ,
            "PATH": os.environ["PATH"],
            "XDG_CONFIG_HOME": str(Path(self.temp_dir.name, "config")),
            "XDG_DATA_HOME": str(Path(self.temp_dir.name, "data")),
            "XDG_CACHE_HOME": str(Path(self.temp_dir.name, "cache")),
            "XDG_RUNTIME_DIR": str(self.run_dir),
            "TERATTS_URL": f"http://127.0.0.1:{self.port}",
            "TERATTS_BEARER_TOKEN": "secret-token-123",
            "TTS_PLUGIN_DIR": str(ROOT),
            "TTS_CONFIG": str(self.config_dir / "config.json"),
            "TTS_DATA_DIR": str(self.data_dir),
            "TTS_SILENT": "1",
        }

    def test_voices_parses_remote_health_endpoint(self):
        res = subprocess.run([TERATTS_PROVIDER, "--voices"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        voices = json.loads(res.stdout)
        self.assertIsInstance(voices, list)
        self.assertEqual(len(voices), 4)
        ru_f1 = next(v for v in voices if v["value"] == "ru_f1")
        self.assertEqual(ru_f1["label"], "Russian Female 1")
        self.assertEqual(ru_f1["language"], "ru")
        eng_f3 = next(v for v in voices if v["value"] == "eng_f3")
        self.assertEqual(eng_f3["language"], "en")

    def test_voices_fallback_when_server_offline(self):
        env = {**self.env, "TERATTS_URL": "http://127.0.0.1:1"}  # non-existent port
        res = subprocess.run([TERATTS_PROVIDER, "--voices"], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        voices = json.loads(res.stdout)
        self.assertTrue(len(voices) >= 10)
        self.assertTrue(any(v["value"] == "ru_f1" for v in voices))

    def test_single_chunk_synthesis_and_payload_structure(self):
        text = "Тестовое предложение для проверки синтеза."
        res = subprocess.run([TERATTS_PROVIDER], input=text, env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(len(self.server.requests), 1)
        req = self.server.requests[0]
        self.assertEqual(req["headers"].get("Authorization"), "Bearer secret-token-123")
        self.assertEqual(req["payload"]["text"], text)
        self.assertEqual(req["payload"]["voice"], "ru_f1")
        self.assertEqual(req["payload"]["language"], "ru")
        self.assertEqual(req["payload"]["duration_scale"], 1.0)
        self.assertEqual(req["payload"]["text_mode"], "russian_only")
        self.assertTrue(req["payload"]["speech_front"])

    def test_rate_duration_scale_inversion(self):
        # Rate 1.5x should yield duration_scale = 1.0 / 1.5 = 0.6667
        env = {**self.env, "TTS_RATE": "1.5"}
        res = subprocess.run([TERATTS_PROVIDER], input="Проверка скорости.", env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(len(self.server.requests), 1)
        scale = self.server.requests[0]["payload"]["duration_scale"]
        self.assertAlmostEqual(scale, 1.0 / 1.5, places=3)

        # Rate 0.5x should yield duration_scale = 2.0
        self.server.requests.clear()
        env["TTS_RATE"] = "0.5"
        res = subprocess.run([TERATTS_PROVIDER], input="Проверка замедления.", env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        scale = self.server.requests[0]["payload"]["duration_scale"]
        self.assertAlmostEqual(scale, 2.0, places=3)

    def test_chunking_long_text_across_sentence_boundaries(self):
        sentences = [
            f"Предложение номер {i} содержит полезную информацию о работе кластера и сервисов."
            for i in range(40)
        ]
        full_text = " ".join(sentences)
        self.assertGreater(len(full_text), 2500)

        res = subprocess.run([TERATTS_PROVIDER], input=full_text, env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertGreater(len(self.server.requests), 1)

        # Ensure no individual chunk exceeds 2000 chars
        for req in self.server.requests:
            chunk_text = req["payload"]["text"]
            self.assertLessEqual(len(chunk_text), 2000)
            self.assertTrue(len(chunk_text.strip()) > 0)

        # Reconstructed text contains all sentences
        all_received = " ".join(r["payload"]["text"] for r in self.server.requests)
        for s in sentences:
            self.assertIn(s, all_received)

    def test_oversized_token_is_hard_cut(self):
        giant_token = "A" * 5000
        res = subprocess.run([TERATTS_PROVIDER], input=giant_token, env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertGreater(len(self.server.requests), 1)
        for req in self.server.requests:
            self.assertLessEqual(len(req["payload"]["text"]), 2000)

    def test_html_tags_and_control_chars_are_sanitized(self):
        messy_input = "<br>Привет!\r\nТекст с <hr>тегами <li>списка</li> и \x00нулевым байтом."
        res = subprocess.run([TERATTS_PROVIDER], input=messy_input, env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        sent_text = self.server.requests[0]["payload"]["text"]
        self.assertNotIn("<br>", sent_text)
        self.assertNotIn("<li>", sent_text)
        self.assertNotIn("\r", sent_text)
        self.assertNotIn("\x00", sent_text)
        self.assertIn("Привет!", sent_text)

    def test_pure_emoji_or_whitespace_exits_cleanly_without_requests(self):
        res = subprocess.run([TERATTS_PROVIDER], input="🎉🚀🎈   ", env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(len(self.server.requests), 0)

    def test_unauthorized_error_returns_exit_77(self):
        env = {**self.env, "TERATTS_BEARER_TOKEN": "wrong-token"}
        res = subprocess.run([TERATTS_PROVIDER], input="Привет", env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 77)
        self.assertIn("HTTP 401", res.stderr)

    def test_server_failure_returns_exit_74(self):
        self.server.fail_with_code = 500
        res = subprocess.run([TERATTS_PROVIDER], input="Привет", env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 74)
        self.assertIn("HTTP 500", res.stderr)

    def test_server_offline_returns_exit_74(self):
        env = {**self.env, "TERATTS_URL": "http://127.0.0.1:1"}
        res = subprocess.run([TERATTS_PROVIDER], input="Привет", env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 74)
        self.assertIn("network error", res.stderr)

    def test_raw_preprocessing_in_bin_speak(self):
        tech_text = "Dynamic Resource Allocation работает в Kubernetes 1.35 с UUID 12345678-1234-5678-1234-567812345678."
        cmd = [
            SPEAK,
            "--provider", "teratts",
            tech_text,
        ]
        res = subprocess.run(cmd, env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(len(self.server.requests), 1)
        received_text = self.server.requests[0]["payload"]["text"]
        self.assertIn("12345678-1234-5678-1234-567812345678", received_text)
        self.assertIn("Kubernetes 1.35", received_text)

    def test_speak_info_and_refresh_voices_teratts(self):
        res = subprocess.run([SPEAK, "--set", ".provider", "teratts"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

        res = subprocess.run([SPEAK, "--refresh-voices", "teratts"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data["provider"], "teratts")
        self.assertEqual(data["count"], 4)

        res = subprocess.run([SPEAK, "--info"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        info = json.loads(res.stdout)
        self.assertEqual(info["provider"], "teratts")
        self.assertEqual(info["voice"], "ru_f1")
        self.assertEqual(len(info["voices"]), 4)
        teratts_meta = next(p for p in info["providers"] if p["name"] == "teratts")
        self.assertEqual(teratts_meta["title"], "TeraTTS")
        self.assertTrue(teratts_meta["refreshVoices"])
        self.assertEqual(teratts_meta["keySource"], "env")

    def test_speak_setup_recognizes_teratts_in_cloud_keys(self):
        # speak-setup key-remove teratts should not reject as unknown provider
        res = subprocess.run([SETUP, "key-remove", "teratts"], env=self.env, capture_output=True, text=True)
        self.assertNotIn("Unknown provider", res.stderr)
        self.assertNotIn("Unknown provider", res.stdout)

if __name__ == "__main__":
    unittest.main()
