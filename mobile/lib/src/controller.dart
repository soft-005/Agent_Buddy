import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import 'buddy_client.dart';
import 'native.dart';
import 'recorder.dart';
import 'settings.dart';
import 'speaker.dart';

class ChatMessage {
  ChatMessage(this.text, {required this.fromUser, this.isError = false, this.toolOk});
  String text;
  final bool fromUser;
  final bool isError;

  /// Set for an action note ("Open Notepad"): true if it was done, false if not.
  final bool? toolOk;
}

/// Buddy is waiting for the user to allow or deny an action (backend/tools.json "ask").
class PendingApproval {
  PendingApproval(this.id, this.title, this.detail, this.deadline);
  final String id;
  final String title;
  final String detail;
  final DateTime deadline;
}

enum LoaderStepState { waiting, active, done, error }

class LoaderStep {
  LoaderStep(this.label);
  String label;
  LoaderStepState state = LoaderStepState.waiting;

  void set(LoaderStepState newState, String newLabel) {
    state = newState;
    label = newLabel;
  }
}

/// Everything the home screen shows, and the conversation logic behind it.
/// Behaves like the desktop app (desktop/renderer/app.js): streamed replies are
/// spoken sentence by sentence with their mood, talking or typing interrupts
/// Buddy, hands-free talking streams the mic while the app is open, and a
/// loader shows startup progress. The conversation itself is saved on the PC,
/// so the chat shows where it left off.
class BuddyController extends ChangeNotifier {
  BuddyController(this.settings);

  final BuddySettings settings;
  final client = BuddyClient();
  final speaker = Speaker();
  final recorder = PushToTalkRecorder();
  late final native = BuddyNative(
    onPushToTalk: (down) => down ? startTalking() : stopTalking(),
    onTalkFromBubble: startTalking,
  );
  PendingApproval? approval;
  String _bubbleState = '';

  final messages = <ChatMessage>[];
  bool needsSetup = false; // no server address yet
  bool listening = false; // holding to talk
  bool replyDone = true;
  bool _muted = false; // the user talked over Buddy: ignore the rest of that reply
  bool _streamed = false;
  ChatMessage? _currentReply;

  /// The mood of the sentence being spoken; [moodSerial] changes with every
  /// sentence so the avatar refreshes it even when the mood stays the same.
  String mood = 'neutral';
  int moodSerial = 0;

  // Microphone, shared by hold-to-talk and hands-free. With "Keep the mic ready"
  // it stays open while the app is in front, and the last half second is kept
  // (never sent) so a recording can start just before the press: the recorder
  // takes a moment to start, and the first word used to get lost.
  bool _micOn = false;
  Future<bool>? _micOpening;
  bool _pttActive = false; // chunks belong to the hold-to-talk recording (until its last one is flushed)
  int _pttBytes = 0;
  final _preroll = Queue<Uint8List>();
  int _prerollBytes = 0;
  static const _prerollMax = PushToTalkRecorder.sampleRate; // bytes: 0.5 s of 16-bit audio

  // Hands-free
  bool _inForeground = true;
  bool _handsFreePaused = false; // Buddy is speaking: don't send audio, or it hears itself
  Timer? _resumeTimer;
  bool heardSpeech = false; // the PC hears speech meant for Buddy
  bool hearing = false; // wake word mode hears someone, maybe not talking to Buddy
  bool _transcribing = false; // speech ended; the transcript comes next

  // Startup loader
  bool loaderVisible = false;
  bool loaderReady = false;
  String loaderTitle = '';
  String loaderHint = '';
  DateTime loaderStarted = DateTime.now();
  final backendStep = LoaderStep('Starting Buddy');
  final sttStep = LoaderStep('Speech recognition');
  final llmStep = LoaderStep('Language model');
  Timer? _statusTimer;
  Timer? _hideLoaderTimer;
  DateTime? _llmOfflineSince;
  static const _lmStudioGrace = Duration(seconds: 30);

  String get avatarState {
    if (listening || heardSpeech) return 'listening';
    if (speaker.speaking.value) return 'speaking';
    if (!replyDone || _transcribing || (loaderVisible && !loaderReady)) return 'thinking';
    return 'idle';
  }

  /// Where the avatar's .vrm comes from (Buddy's backend serves it).
  String? get avatarUrl => avatarUri(settings.server, settings.avatar)?.toString();

  bool get handsFreeOn => settings.handsFree != 'off';
  bool get _streaming => handsFreeOn && _inForeground; // mic audio goes to the PC continuously
  bool get _keepMicOpen => _inForeground && (handsFreeOn || settings.micReady);

  Future<void> init() async {
    await speaker.init();
    await speaker.apply(settings);
    speaker.speaking.addListener(_onSpeaking);
    speaker.onMood = (m) {
      mood = m;
      moodSerial++;
      notifyListeners();
    };
    client.connection.addListener(_onConnection);
    client.events.listen(_onMessage);
    addListener(_syncBubble);
    applyKeepScreenOn();
    await applyGestures();
    connect();
    unawaited(applyMic()); // opens the mic if it should stay ready
    // Opened by a long-press on the floating bubble: start listening right away.
    if (await native.takeLaunchAction() == 'talk') startTalking();
  }

  /// Volume key and floating bubble, from the settings.
  Future<void> applyGestures() async {
    await native.setVolumeKeyTalk(settings.volumeKeyTalk);
    await native.setBubbleEnabled(settings.floatingBubble && await native.canDrawOverlays());
  }

  // Keep the floating bubble's ring in step with Buddy's state.
  void _syncBubble() {
    final state = approval != null ? 'approval' : avatarState;
    if (state != _bubbleState) {
      _bubbleState = state;
      native.updateBubble(state);
    }
  }

  void applyKeepScreenOn() {
    WakelockPlus.toggle(enable: settings.keepScreenOn);
  }

  /// (Re)connects to the server in the settings, or asks for one.
  void connect() {
    final uri = webSocketUri(settings.server);
    if (uri == null) {
      needsSetup = true;
      client.close();
      notifyListeners();
      return;
    }
    needsSetup = false;
    _showLoader('Buddy is waking up…');
    client.connect(uri);
  }

  /// Switch to another avatar file on the PC ('' = the PC's default). The avatar view reloads it.
  void setAvatar(String name) {
    settings.avatar = name;
    notifyListeners();
  }

  /// The avatars Buddy's PC offers, or null if they can't be listed (offline, older backend).
  Future<List<String>?> fetchAvatars() async {
    final uri = httpUri(settings.server, '/avatars');
    if (uri == null) return null;
    final http = HttpClient()..connectionTimeout = const Duration(seconds: 4);
    try {
      final response = await (await http.getUrl(uri)).close().timeout(const Duration(seconds: 6));
      if (response.statusCode != 200) return null;
      final body = jsonDecode(await response.transform(utf8.decoder).join());
      return body is Map && body['avatars'] is List ? (body['avatars'] as List).whereType<String>().toList() : null;
    } catch (_) {
      return null;
    } finally {
      http.close(force: true);
    }
  }

  /// Start a new conversation: the PC keeps the old one in backend/data/conversations.
  void newConversation() {
    _interrupt();
    client.sendJson({'type': 'reset'});
    messages.clear();
    _currentReply = null;
    notifyListeners();
  }

  // ---------- connection and loader ----------

  void _onConnection() {
    final state = client.connection.value;
    if (state == Connection.connected) {
      client.sendJson({'type': 'history'}); // pick up the saved conversation
      if (_streaming) client.sendJson({'type': 'listen', 'mode': settings.handsFree});
      if (loaderVisible && !loaderReady) {
        backendStep.set(LoaderStepState.done, 'Connected to Buddy');
        _pollStatus();
      }
    } else if (state == Connection.disconnected) {
      _statusTimer?.cancel();
      if (!replyDone) replyDone = true; // the backend went away mid-reply
      heardSpeech = hearing = _transcribing = false;
      if (!loaderVisible || loaderReady) {
        _showLoader('Reconnecting to Buddy…');
      } else {
        backendStep.set(LoaderStepState.active, 'Connecting to Buddy');
      }
    }
    notifyListeners();
  }

  void _showLoader(String title) {
    _hideLoaderTimer?.cancel();
    loaderVisible = true;
    loaderReady = false;
    loaderTitle = title;
    loaderHint = '';
    loaderStarted = DateTime.now();
    _llmOfflineSince = null;
    backendStep.set(LoaderStepState.active, 'Connecting to Buddy');
    sttStep.set(LoaderStepState.waiting, 'Speech recognition');
    llmStep.set(LoaderStepState.waiting, 'Language model');
    notifyListeners();
  }

  void _pollStatus() {
    _statusTimer?.cancel();
    client.sendJson({'type': 'status'});
    _statusTimer = Timer.periodic(const Duration(seconds: 1), (_) => client.sendJson({'type': 'status'}));
  }

  void _applyStatus(Map<String, dynamic> status) {
    if (!loaderVisible || loaderReady) return;
    final model = (status['model_name'] as String?) ?? 'the language model';
    var hint = '';

    switch (status['stt']) {
      case 'ready':
        sttStep.set(LoaderStepState.done, 'Speech recognition ready');
      case 'error':
        sttStep.set(LoaderStepState.error, 'Speech recognition failed');
      default:
        sttStep.set(LoaderStepState.active, 'Loading speech recognition');
    }

    final llm = status['llm'];
    if (llm == 'offline') {
      _llmOfflineSince ??= DateTime.now();
      if (DateTime.now().difference(_llmOfflineSince!) < _lmStudioGrace) {
        llmStep.set(LoaderStepState.active, 'Waiting for LM Studio');
      } else {
        llmStep.set(LoaderStepState.error, "LM Studio isn't running");
        hint = 'On the PC, open LM Studio and start its server.';
      }
    } else {
      _llmOfflineSince = null;
      if (llm == 'ready') {
        llmStep.set(LoaderStepState.done, '$model ready');
      } else if (llm == 'missing') {
        llmStep.set(LoaderStepState.error, "$model isn't in LM Studio");
        hint = 'Download it in LM Studio, or change LLM_MODEL on the PC.';
      } else {
        llmStep.set(LoaderStepState.active, 'Loading $model');
        hint = 'Loading the model on the PC can take a minute or two.';
      }
    }
    loaderHint = hint;

    // Speech recognition failing doesn't block Buddy: typing still works.
    if (status['stt'] != 'loading' && llm == 'ready') {
      if (status['stt'] == 'error') {
        messages.add(
          ChatMessage(
            "Speech recognition didn't load on the PC, so voice won't work. Typing still does.",
            fromUser: false,
            isError: true,
          ),
        );
      }
      _finishLoading();
    }
    notifyListeners();
  }

  void _finishLoading() {
    _statusTimer?.cancel();
    loaderReady = true;
    loaderTitle = 'Ready!';
    loaderHint = '';
    _hideLoaderTimer = Timer(const Duration(milliseconds: 1400), () {
      loaderVisible = false;
      notifyListeners();
    });
  }

  // ---------- messages from Buddy ----------

  void _onMessage(Map<String, dynamic> msg) {
    switch (msg['type']) {
      case 'status':
        _applyStatus(msg);
        return;
      case 'history':
        // The saved conversation (on connect), or [] after a reset. Not mid-turn.
        if (!replyDone || listening) return;
        messages
          ..clear()
          ..addAll([
            for (final line in (msg['messages'] as List? ?? []).whereType<Map>())
              ChatMessage('${line['text'] ?? ''}', fromUser: line['role'] == 'user'),
          ]);
        _currentReply = null;
      case 'speech':
        // Hands-free: the PC hears speech starting or ending.
        final active = msg['active'] == true;
        if (msg['addressed'] != true) {
          hearing = active; // wake word mode: it may not be for Buddy
        } else if (active) {
          hearing = false;
          heardSpeech = true;
          if (approval == null) {
            // The PC replaces the old turn once this one is transcribed. (While
            // Buddy waits for permission, a spoken yes or no answers it instead.)
            speaker.stop();
            if (!replyDone) _muted = true;
          }
        } else if (heardSpeech) {
          heardSpeech = false;
          _transcribing = true;
        }
      case 'transcript':
        _transcribing = false;
        final text = (msg['text'] as String?)?.trim() ?? '';
        if (text.isNotEmpty) {
          // Hands-free: a new turn starts now, so drop the old reply.
          speaker.stop();
          approval = null;
          if (!replyDone) _muted = true;
          messages.add(ChatMessage(text, fromUser: true));
        }
      case 'thinking':
        _transcribing = false;
        replyDone = false;
        _muted = false;
        _streamed = false;
        _currentReply = null;
      case 'reply_chunk':
        if (_muted) return;
        _streamed = true;
        final sentence = msg['text'] as String? ?? '';
        final reply = _currentReply ??= _addReply();
        reply.text = reply.text.isEmpty ? sentence : '${reply.text} $sentence';
        // A chunk with "audio": true would come with server speech; the phone uses its own voice.
        speaker.say(sentence, msg['mood'] as String?);
        native.updateBubble(avatarState, sentence);
      case 'reply':
        if (_muted) return;
        approval = null;
        final text = msg['text'] as String? ?? '';
        if (_streamed) {
          (_currentReply ??= _addReply()).text = text;
        } else {
          _addReply().text = text;
          speaker.say(text, msg['mood'] as String?);
          native.updateBubble(avatarState, text);
        }
        replyDone = true;
      case 'error':
        approval = null;
        _transcribing = false;
        messages.add(ChatMessage(msg['message'] as String? ?? 'Something went wrong', fromUser: false, isError: true));
        replyDone = true;
      case 'approval':
        if (_muted) return;
        final title = msg['title'] as String? ?? 'do something';
        final seconds = (msg['timeout'] as num?)?.toDouble() ?? 60;
        approval = PendingApproval(
          msg['id'] as String,
          title,
          msg['detail'] as String? ?? '',
          DateTime.now().add(Duration(milliseconds: (seconds * 1000).round())),
        );
        speaker.say('May I ${title[0].toLowerCase()}${title.substring(1)}?');
        native.updateBubble('approval', 'Buddy needs your OK: $title. Tap to answer.');
      case 'approval_closed':
        // "timeout", or "answered" by voice (hands-free)
        if (approval?.id == msg['id']) {
          approval = null;
          if (msg['reason'] == 'timeout') messages.add(ChatMessage('No answer in time', fromUser: false, toolOk: false));
        }
      case 'tool':
        if (_muted) return;
        // Before the reply's words, so the note sits above them.
        final note = ChatMessage(msg['title'] as String? ?? 'Action', fromUser: false, toolOk: msg['ok'] == true);
        final reply = _currentReply;
        if (reply != null && reply.text.isEmpty) {
          messages.insert(messages.length - 1, note);
        } else {
          messages.add(note);
          _currentReply = null; // words after the action start a new bubble
        }
    }
    notifyListeners();
  }

  ChatMessage _addReply() {
    final reply = ChatMessage('', fromUser: false);
    messages.add(reply);
    return reply;
  }

  // ---------- talking and typing ----------

  /// The user starts a new turn: cut Buddy off and drop the rest of its reply.
  void _interrupt() {
    speaker.stop();
    _resumeHandsFree(followup: false);
    approval = null; // the backend cancels the turn, which withdraws the request
    if (!replyDone) _muted = true;
    client.sendJson({'type': 'cancel'});
  }

  void sendText(String text) {
    text = text.trim();
    if (text.isEmpty) return;
    _interrupt();
    messages.add(ChatMessage(text, fromUser: true));
    client.sendJson({'type': 'text', 'text': text});
    notifyListeners();
  }

  Future<void> startTalking() async {
    if (listening) return;
    _interrupt();
    listening = true;
    _pttActive = true;
    _pttBytes = 0;
    if (_streaming) client.sendJson({'type': 'listen', 'mode': 'push'}); // until released
    // The half second before the press (when the mic was already open). Not counted
    // in _pttBytes, so a quick accidental tap is still discarded.
    for (final chunk in _preroll) {
      client.sendAudio(chunk);
    }
    _clearPreroll();
    notifyListeners();
    if (!await _openMic()) {
      listening = _pttActive = false;
      messages.add(ChatMessage('Buddy needs microphone permission to hear you.', fromUser: false, isError: true));
      notifyListeners();
    } else if (!listening && !_keepMicOpen) {
      await _closeMic(); // released before the mic was ready
    }
  }

  Future<void> stopTalking() async {
    if (!listening) return;
    listening = false;
    notifyListeners();
    if (!_keepMicOpen) await _closeMic(); // flushes the last chunk into the recording
    _pttActive = false;
    final seconds = _pttBytes / 2 / PushToTalkRecorder.sampleRate;
    // Under 0.3 s is an accidental tap: discard it on the backend too.
    client.sendJson({'type': seconds < 0.3 ? 'cancel' : 'end'});
    if (_streaming) client.sendJson({'type': 'listen', 'mode': settings.handsFree});
  }

  // ---------- microphone and hands-free ----------

  Future<bool> _openMic() {
    if (_micOn) return Future.value(true);
    return _micOpening ??= recorder.start(_onMicChunk).then((ok) => _micOn = ok).whenComplete(() => _micOpening = null);
  }

  Future<void> _closeMic() async {
    await _micOpening;
    if (!_micOn) return;
    _micOn = false;
    await recorder.stop();
    _clearPreroll();
  }

  void _clearPreroll() {
    _preroll.clear();
    _prerollBytes = 0;
  }

  void _onMicChunk(Uint8List chunk) {
    if (_pttActive) {
      _pttBytes += chunk.length;
      client.sendAudio(chunk);
    } else if (_streaming && !_handsFreePaused) {
      client.sendAudio(chunk); // the PC finds where speech starts and ends
    } else if (speaker.speaking.value) {
      _clearPreroll(); // don't start a recording with Buddy's own voice
    } else {
      _preroll.add(chunk);
      _prerollBytes += chunk.length;
      while (_prerollBytes > _prerollMax && _preroll.length > 1) {
        _prerollBytes -= _preroll.removeFirst().length;
      }
    }
  }

  /// Hands-free or "Keep the mic ready" changed in the settings, or the app came
  /// to the front or went away. The mic stays open only while the app is in front
  /// (Android doesn't let a regular app record in the background).
  Future<void> applyMic() async {
    client.sendJson({'type': 'listen', 'mode': _streaming ? settings.handsFree : 'push'});
    if (!_streaming) heardSpeech = hearing = false;
    if (_keepMicOpen) {
      if (!await _openMic()) {
        messages.add(ChatMessage('Buddy needs microphone permission to hear you.', fromUser: false, isError: true));
      }
    } else if (!listening) {
      await _closeMic();
    }
    notifyListeners();
  }

  void setForeground(bool foreground) {
    if (foreground == _inForeground) return;
    _inForeground = foreground;
    applyMic();
  }

  // While Buddy speaks the mic audio isn't sent, so Buddy doesn't hear itself.
  void _onSpeaking() {
    _resumeTimer?.cancel();
    if (speaker.speaking.value) {
      _handsFreePaused = true;
    } else if (_handsFreePaused) {
      // Let the last sound fade; after a finished reply the follow-up window opens.
      _resumeTimer = Timer(const Duration(milliseconds: 400), () => _resumeHandsFree(followup: replyDone));
    }
    notifyListeners();
  }

  void _resumeHandsFree({required bool followup}) {
    _resumeTimer?.cancel();
    if (!_handsFreePaused) return;
    _handsFreePaused = false;
    if (_streaming) client.sendJson({'type': 'listen', 'mode': settings.handsFree, 'followup': followup});
  }

  /// The user answered Buddy's approval request.
  void answerApproval(bool approved) {
    final pending = approval;
    if (pending == null) return;
    client.sendJson({'type': 'approval', 'id': pending.id, 'approved': approved});
    approval = null;
    notifyListeners();
  }

  /// Double-tap on the avatar: stop Buddy talking (and stop the reply on the PC).
  void stopBuddy() {
    _interrupt();
    notifyListeners();
  }

  Future<void> applyVoiceSettings() => speaker.apply(settings);

  Future<void> testVoice() async {
    await speaker.stop();
    speaker.say('Hi! This is how I sound now.', 'happy');
  }

  @override
  void dispose() {
    _statusTimer?.cancel();
    _hideLoaderTimer?.cancel();
    _resumeTimer?.cancel();
    client.close();
    speaker.stop();
    recorder.dispose();
    WakelockPlus.disable();
    super.dispose();
  }
}
