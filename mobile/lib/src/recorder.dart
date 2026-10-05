import 'dart:async';
import 'dart:typed_data';

import 'package:record/record.dart';

/// Microphone streaming for hold-to-talk and hands-free talking: 16 kHz mono
/// 16-bit PCM, in chunks as the backend expects (same format as the desktop app).
class PushToTalkRecorder {
  final AudioRecorder _recorder = AudioRecorder();
  StreamSubscription<Uint8List>? _subscription;
  int _bytes = 0;

  static const sampleRate = 16000;

  /// Starts recording; each chunk goes to [onChunk]. Returns false without
  /// microphone permission.
  Future<bool> start(void Function(Uint8List chunk) onChunk) async {
    if (!await _recorder.hasPermission()) return false;
    _bytes = 0;
    final stream = await _recorder.startStream(
      const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: sampleRate,
        numChannels: 1,
        autoGain: true,
        echoCancel: true,
        noiseSuppress: true,
        // The default (pause) asks for audio focus and pauses on any focus loss,
        // even a notification sound, and never resumes: the open mic then went
        // silent for good. Buddy manages talking and listening itself.
        audioInterruption: AudioInterruptionMode.none,
      ),
    );
    _subscription = stream.listen((chunk) {
      _bytes += chunk.length;
      onChunk(chunk);
    });
    return true;
  }

  /// Stops recording and returns how many seconds were captured.
  Future<double> stop() async {
    await _recorder.stop(); // flushes the last chunk to the stream
    await _subscription?.cancel();
    _subscription = null;
    return _bytes / 2 / sampleRate;
  }

  Future<void> dispose() => _recorder.dispose();
}
