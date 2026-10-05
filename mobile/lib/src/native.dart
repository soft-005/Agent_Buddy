import 'package:flutter/services.dart';

/// Android-only features in MainActivity.kt and BuddyBubble.kt: the Volume Down
/// hold-to-talk gesture and the floating bubble shown over other apps.
class BuddyNative {
  BuddyNative({required this.onPushToTalk, required this.onTalkFromBubble}) {
    _channel.setMethodCallHandler((call) async {
      switch (call.method) {
        case 'pushToTalk':
          onPushToTalk(call.arguments == true);
        case 'startTalkingFromBubble':
          onTalkFromBubble();
      }
    });
  }

  static const _channel = MethodChannel('buddy/native');

  /// true when Volume Down is pressed long enough, false when it's released.
  final void Function(bool down) onPushToTalk;

  /// The bubble was long-pressed: start listening.
  final void Function() onTalkFromBubble;

  Future<void> setVolumeKeyTalk(bool enabled) => _call('setVolumeKeyTalk', enabled);

  Future<bool> canDrawOverlays() async => await _call<bool>('canDrawOverlays') ?? false;

  /// Opens Android's "Display over other apps" page for Buddy.
  Future<void> requestOverlayPermission() => _call('requestOverlayPermission');

  Future<void> setBubbleEnabled(bool enabled) => _call('setBubbleEnabled', enabled);

  /// state: idle, listening, thinking, speaking or approval. text: a new line to show.
  Future<void> updateBubble(String state, [String? text]) => _call('updateBubble', {'state': state, 'text': text});

  /// "talk" if the app was opened by a long-press on the bubble.
  Future<String?> takeLaunchAction() => _call<String>('takeLaunchAction');

  Future<T?> _call<T>(String method, [Object? arguments]) async {
    try {
      return await _channel.invokeMethod<T>(method, arguments);
    } on MissingPluginException {
      return null; // tests and non-Android platforms
    } on PlatformException {
      return null;
    }
  }
}
