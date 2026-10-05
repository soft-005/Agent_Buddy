# Buddy for Android

A Flutter app that talks to Buddy's backend on your PC over Wi-Fi. It has the same avatar, model and memory as the desktop app.

## Use it

1. On the PC, set `LAN_ACCESS=true` in `backend/.env` and start Buddy with the desktop shortcut. Allow Python through Windows Firewall on private networks when asked.
2. Install the APK on the phone: `build/app/outputs/flutter-apk/app-arm64-v8a-release.apk`.
3. Open Buddy, tap **Find on Wi-Fi** (or type the PC's address, for example `192.168.1.134`) and **Connect**.
4. Hold the mic button to talk, or type. Settings has the avatar, hands-free talking, the voice, speed and pitch.

Hands-free talking works while the app is open. **Always** answers whenever you stop talking. **Wake word** answers only when you start or end with "Hey Buddy", and for a few seconds after each reply. The mic button gets a green ring while it's on. The chat picks up the conversation saved on the PC. The top bar's speech-bubble button starts a new one.

## Build

```
flutter pub get
flutter build apk --release --split-per-abi   # phone install
flutter run                                   # connected phone or emulator
flutter run --dart-define=BUDDY_SERVER=10.0.2.2   # emulator: the PC is 10.0.2.2
flutter analyze; flutter test
```

The avatar page in `assets/avatar/` is built from the desktop's `avatar.js`. After changing that file, run `cd avatar_web && npm install && npm run build`. It needs `desktop/node_modules`.

More detail, including the protocol and conventions, is in the project's `CLAUDE.md`.
