import 'dart:collection';

import 'package:flutter/foundation.dart';
import 'package:flutter_tts/flutter_tts.dart';

import 'settings.dart';

/// Speaks Buddy's reply sentence by sentence with the phone's text-to-speech,
/// in the order the sentences arrive.
class Speaker {
  final FlutterTts _tts = FlutterTts();
  final speaking = ValueNotifier<bool>(false);
  final _queue = Queue<(String, String?)>();
  bool _busy = false;
  int _generation = 0; // bumped by stop() so an old queue run ends quietly

  /// Called as a sentence that came with a mood starts, so the avatar's face can follow.
  void Function(String mood)? onMood;

  Future<void> init() async {
    await _tts.awaitSpeakCompletion(true); // speak() returns when the sentence ends
  }

  Future<void> apply(BuddySettings settings) async {
    await _tts.setSpeechRate(settings.rate);
    await _tts.setPitch(settings.pitch);
    await _tts.setVolume(1.0);
    final voice = settings.voice.split('|');
    if (voice.length == 2) {
      await _tts.setVoice({'name': voice[0], 'locale': voice[1]});
    }
  }

  /// Installed voices as "name|locale", English ones first.
  Future<List<String>> voices() async {
    final raw = await _tts.getVoices;
    if (raw is! List) return [];
    final voices = <String>[
      for (final v in raw)
        if (v is Map && v['name'] != null && v['locale'] != null) '${v['name']}|${v['locale']}',
    ];
    voices.sort((a, b) {
      final aEnglish = a.split('|').last.startsWith('en');
      final bEnglish = b.split('|').last.startsWith('en');
      if (aEnglish != bEnglish) return aEnglish ? -1 : 1;
      return a.compareTo(b);
    });
    return voices;
  }

  /// Queues [text]. [mood] (from the backend) is shown while it's spoken.
  void say(String text, [String? mood]) {
    if (text.trim().isEmpty) return;
    _queue.add((text, mood));
    _run();
  }

  Future<void> _run() async {
    if (_busy) return;
    _busy = true;
    final generation = _generation;
    while (_queue.isNotEmpty && generation == _generation) {
      final (text, mood) = _queue.removeFirst();
      if (mood != null) onMood?.call(mood);
      speaking.value = true;
      await _tts.speak(text);
    }
    if (generation == _generation) {
      _busy = false;
      speaking.value = false;
    }
  }

  Future<void> stop() async {
    _generation++;
    _queue.clear();
    _busy = false;
    speaking.value = false;
    await _tts.stop();
  }
}
