import 'package:buddy_mobile/src/settings.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('server address', () {
    test('a bare IP gets the default port and /ws', () {
      expect(webSocketUri('192.168.1.134').toString(), 'ws://192.168.1.134:8818/ws');
    });
    test('host:port keeps the port', () {
      expect(webSocketUri('10.0.2.2:9000').toString(), 'ws://10.0.2.2:9000/ws');
    });
    test('a full URL is kept', () {
      expect(webSocketUri('ws://pc.local:8818/ws').toString(), 'ws://pc.local:8818/ws');
    });
    test('empty or junk is rejected', () {
      expect(webSocketUri(''), isNull);
      expect(webSocketUri('   '), isNull);
    });
    test('avatar URL is on the same server over http', () {
      expect(httpUri('192.168.1.134', '/avatar.vrm').toString(), 'http://192.168.1.134:8818/avatar.vrm');
    });
    test('no avatar chosen: the PC default', () {
      expect(avatarUri('192.168.1.134', '').toString(), 'http://192.168.1.134:8818/avatar.vrm');
    });
    test('a chosen avatar is fetched by name, encoded', () {
      expect(avatarUri('192.168.1.134', 'My Model.vrm').toString(), 'http://192.168.1.134:8818/avatars/My%20Model.vrm');
    });
  });
}
