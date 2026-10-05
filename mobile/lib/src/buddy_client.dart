import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/io.dart';

enum Connection { disconnected, connecting, connected }

/// The WebSocket link to Buddy's backend (protocol: see CLAUDE.md).
/// Reconnects every 2 seconds until [close] is called or the address changes.
class BuddyClient {
  final connection = ValueNotifier<Connection>(Connection.disconnected);
  final _events = StreamController<Map<String, dynamic>>.broadcast();

  /// JSON messages from the server, decoded.
  Stream<Map<String, dynamic>> get events => _events.stream;

  IOWebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;
  Timer? _retry;
  Uri? _uri;
  int _generation = 0; // ignores callbacks from a connection we already replaced

  bool get isConnected => connection.value == Connection.connected;

  void connect(Uri uri) {
    _uri = uri;
    _open();
  }

  Future<void> _open() async {
    final generation = ++_generation;
    _retry?.cancel();
    await _teardown();
    final uri = _uri;
    if (uri == null) return;
    connection.value = Connection.connecting;
    try {
      final channel = IOWebSocketChannel.connect(
        uri,
        connectTimeout: const Duration(seconds: 5),
        pingInterval: const Duration(seconds: 20),
      );
      await channel.ready;
      if (generation != _generation) {
        channel.sink.close();
        return;
      }
      _channel = channel;
      connection.value = Connection.connected;
      _subscription = channel.stream.listen(
        (data) {
          // Binary frames would be server-side speech audio; this app speaks
          // with the phone's own voice, so only JSON messages are used.
          if (data is String) {
            try {
              _events.add(jsonDecode(data) as Map<String, dynamic>);
            } catch (_) {}
          }
        },
        onDone: () => _lost(generation),
        onError: (_) => _lost(generation),
        cancelOnError: true,
      );
    } catch (_) {
      _lost(generation);
    }
  }

  void _lost(int generation) {
    if (generation != _generation) return;
    _channel = null;
    connection.value = Connection.disconnected;
    _retry?.cancel();
    if (_uri != null) _retry = Timer(const Duration(seconds: 2), _open);
  }

  Future<void> _teardown() async {
    await _subscription?.cancel();
    _subscription = null;
    await _channel?.sink.close();
    _channel = null;
  }

  void sendJson(Map<String, dynamic> message) {
    if (isConnected) _channel?.sink.add(jsonEncode(message));
  }

  void sendAudio(Uint8List pcm) {
    if (isConnected) _channel?.sink.add(pcm);
  }

  Future<void> close() async {
    _uri = null;
    _generation++;
    _retry?.cancel();
    await _teardown();
    connection.value = Connection.disconnected;
  }
}
