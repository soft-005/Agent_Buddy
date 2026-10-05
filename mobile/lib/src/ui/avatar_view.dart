import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import '../buddy_client.dart';
import '../controller.dart';

/// Buddy's 3D avatar: the desktop avatar code running in a transparent web view
/// (assets/avatar). The .vrm model is downloaded from Buddy's backend.
class AvatarView extends StatefulWidget {
  const AvatarView({super.key, required this.controller});

  final BuddyController controller;

  @override
  State<AvatarView> createState() => _AvatarViewState();
}

class _AvatarViewState extends State<AvatarView> {
  InAppWebViewController? _web;
  bool _pageReady = false;
  bool _modelLoaded = false;
  String? _sentState;
  String? _sentUrl;
  int? _sentMood;

  BuddyController get _c => widget.controller;

  @override
  void initState() {
    super.initState();
    _c.addListener(_sync);
    _c.client.connection.addListener(_onConnection);
  }

  @override
  void dispose() {
    _c.removeListener(_sync);
    _c.client.connection.removeListener(_onConnection);
    super.dispose();
  }

  Future<void> _js(String source) async {
    try {
      await _web?.evaluateJavascript(source: source);
    } catch (_) {}
  }

  void _sync() {
    if (!_pageReady) return;
    if (_c.avatarUrl != _sentUrl) {
      _reload(); // the server address or the chosen avatar changed
      return;
    }
    // Mood first: a sentence's mood and the "speaking" state usually change together.
    if (_c.moodSerial != _sentMood) {
      _sentMood = _c.moodSerial;
      _js('window.buddyAvatar && buddyAvatar.setMood(${jsonEncode(_c.mood)})');
    }
    final state = _c.avatarState;
    if (state != _sentState) {
      _sentState = state;
      _js("window.buddyAvatar && buddyAvatar.setState('$state')");
    }
  }

  // If the model couldn't be downloaded (backend offline), try again once connected.
  void _onConnection() {
    if (_c.client.connection.value == Connection.connected && _pageReady && !_modelLoaded) _reload();
  }

  void _reload() {
    _pageReady = false;
    _web?.reload();
  }

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: InAppWebView(
        initialFile: 'assets/avatar/index.html',
        initialSettings: InAppWebViewSettings(
          transparentBackground: true,
          allowFileAccessFromFileURLs: true,
          mediaPlaybackRequiresUserGesture: false,
          disableContextMenu: true,
          supportZoom: false,
          verticalScrollBarEnabled: false,
          horizontalScrollBarEnabled: false,
        ),
        onWebViewCreated: (controller) => _web = controller,
        onLoadStop: (controller, url) async {
          _pageReady = true;
          _modelLoaded = false;
          _sentUrl = _c.avatarUrl;
          _sentState = null;
          _sentMood = _c.moodSerial; // a fresh page starts neutral; the next sentence sets a mood
          await _js('window.buddyAvatar && buddyAvatar.setAvatarUrl(${jsonEncode(_sentUrl)})');
          _sync();
        },
        onConsoleMessage: (controller, message) {
          if (message.message.startsWith('Loaded avatar')) _modelLoaded = true;
          if (kDebugMode) debugPrint('[avatar] ${message.message}');
        },
      ),
    );
  }
}
