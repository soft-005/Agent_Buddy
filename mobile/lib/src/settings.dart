import 'package:shared_preferences/shared_preferences.dart';

/// App settings, saved on the phone.
class BuddySettings {
  BuddySettings._(this._prefs);

  final SharedPreferences _prefs;

  static Future<BuddySettings> load() async => BuddySettings._(await SharedPreferences.getInstance());

  /// The PC running Buddy's backend: "192.168.1.134", "192.168.1.134:8818",
  /// or a full ws:// URL. A build can preset it with --dart-define=BUDDY_SERVER=...
  String get server => _prefs.getString('server') ?? const String.fromEnvironment('BUDDY_SERVER');
  set server(String value) => _prefs.setString('server', value.trim());

  /// Text-to-speech voice as "name|locale"; empty = the phone's default voice.
  String get voice => _prefs.getString('voice') ?? '';
  set voice(String value) => _prefs.setString('voice', value);

  /// Android speech rate: 0.5 is normal speed.
  double get rate => _prefs.getDouble('rate') ?? 0.5;
  set rate(double value) => _prefs.setDouble('rate', value);

  /// Voice pitch: 1.0 is normal.
  double get pitch => _prefs.getDouble('pitch') ?? 1.0;
  set pitch(double value) => _prefs.setDouble('pitch', value);

  /// Keep the screen on while Buddy is open.
  bool get keepScreenOn => _prefs.getBool('keepScreenOn') ?? true;
  set keepScreenOn(bool value) => _prefs.setBool('keepScreenOn', value);

  /// Hold Volume Down to talk while Buddy is open (a short press still lowers the volume).
  bool get volumeKeyTalk => _prefs.getBool('volumeKeyTalk') ?? true;
  set volumeKeyTalk(bool value) => _prefs.setBool('volumeKeyTalk', value);

  /// Show Buddy's head over other apps while Buddy is in the background.
  bool get floatingBubble => _prefs.getBool('floatingBubble') ?? false;
  set floatingBubble(bool value) => _prefs.setBool('floatingBubble', value);

  /// Keep the mic open while the app is open, so a recording can start with the
  /// half second before you pressed (nothing is sent until you talk).
  bool get micReady => _prefs.getBool('micReady') ?? true;
  set micReady(bool value) => _prefs.setBool('micReady', value);

  /// Avatar file on the PC (from /avatars); empty = the PC's default avatar.
  String get avatar => _prefs.getString('avatar') ?? '';
  set avatar(String value) => _prefs.setString('avatar', value);

  /// Hands-free talking while the app is open: "off", "vad" (always listening)
  /// or "wake" (only after the wake word, "Hey Buddy").
  String get handsFree {
    final value = _prefs.getString('handsFree');
    return handsFreeModes.contains(value) ? value! : 'off';
  }

  set handsFree(String value) => _prefs.setString('handsFree', value);
}

const handsFreeModes = ['off', 'vad', 'wake'];

const defaultPort = 8818; // the backend's default PORT

/// ws://host:port/ws for a server setting, or null if it isn't usable.
Uri? webSocketUri(String server) {
  var text = server.trim();
  if (text.isEmpty) return null;
  if (!text.contains('://')) text = 'ws://$text';
  final uri = Uri.tryParse(text);
  if (uri == null || uri.host.isEmpty) return null;
  final secure = uri.scheme == 'wss' || uri.scheme == 'https';
  return Uri(
    scheme: secure ? 'wss' : 'ws',
    host: uri.host,
    port: uri.hasPort ? uri.port : defaultPort,
    path: (uri.path.isEmpty || uri.path == '/') ? '/ws' : uri.path,
  );
}

/// http://host:port + [path] on the same server, e.g. for /avatar.vrm.
Uri? httpUri(String server, String path) {
  final ws = webSocketUri(server);
  if (ws == null) return null;
  return ws.replace(scheme: ws.scheme == 'wss' ? 'https' : 'http', path: path);
}

/// Where to download an avatar: the PC's default for an empty [name], else /avatars/[name].
Uri? avatarUri(String server, String name) {
  final base = httpUri(server, '/avatar.vrm');
  if (base == null || name.isEmpty) return base;
  return base.replace(pathSegments: ['avatars', name]); // encodes spaces and the like
}
