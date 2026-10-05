import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'settings.dart';

/// Finds Buddy's backend on the phone's Wi-Fi network by asking every address
/// in the same /24 subnet for /health. Takes a few seconds. The backend must
/// run with LAN_ACCESS=true. On the Android emulator this finds the PC at 10.0.2.2.
Future<List<String>> findBuddy({int port = defaultPort}) async {
  final interfaces = await NetworkInterface.list(type: InternetAddressType.IPv4, includeLoopback: false);
  final prefixes = <String>{
    for (final interface in interfaces)
      for (final address in interface.addresses)
        if (!address.isLinkLocal) address.address.substring(0, address.address.lastIndexOf('.') + 1),
  };

  final candidates = [
    for (final prefix in prefixes)
      for (var i = 1; i < 255; i++) '$prefix$i',
  ];

  final found = <String>[];
  const batchSize = 48;
  for (var start = 0; start < candidates.length; start += batchSize) {
    final batch = candidates.skip(start).take(batchSize);
    final results = await Future.wait(batch.map((host) => _isBuddy(host, port)));
    for (final (i, ok) in results.indexed) {
      if (ok) found.add(port == defaultPort ? batch.elementAt(i) : '${batch.elementAt(i)}:$port');
    }
  }
  return found;
}

Future<bool> _isBuddy(String host, int port) async {
  final client = HttpClient()..connectionTimeout = const Duration(milliseconds: 700);
  try {
    final request = await client.getUrl(Uri(scheme: 'http', host: host, port: port, path: '/health'));
    final response = await request.close().timeout(const Duration(seconds: 2));
    if (response.statusCode != 200) return false;
    final body = jsonDecode(await response.transform(utf8.decoder).join().timeout(const Duration(seconds: 2)));
    return body is Map && body['ok'] == true && body.containsKey('model');
  } catch (_) {
    return false;
  } finally {
    client.close(force: true);
  }
}
