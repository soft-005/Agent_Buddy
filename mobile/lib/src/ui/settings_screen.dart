import 'package:flutter/material.dart';

import '../controller.dart';
import 'server_form.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.controller});

  final BuddyController controller;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> with WidgetsBindingObserver {
  List<String> _voices = [];
  bool _overlayAllowed = false;
  List<String>? _avatars; // null while loading or when the PC can't be reached
  bool _avatarsLoading = true;

  BuddyController get _c => widget.controller;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _checkOverlay();
    _c.speaker.voices().then((voices) {
      if (mounted) setState(() => _voices = voices);
    });
    _c.fetchAvatars().then((avatars) {
      if (mounted) {
        setState(() {
          _avatars = avatars;
          _avatarsLoading = false;
        });
      }
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  // Back from Android's "Display over other apps" page: pick up the new permission.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _checkOverlay();
  }

  Future<void> _checkOverlay() async {
    final allowed = await _c.native.canDrawOverlays();
    await _c.applyGestures();
    if (mounted) setState(() => _overlayAllowed = allowed);
  }

  Future<void> _changed() async {
    await _c.applyVoiceSettings();
    setState(() {});
  }

  /// The .vrm files in the PC's desktop/assets/models folder.
  Widget _avatarPicker() {
    final avatars = _avatars;
    if (avatars == null) {
      return Text(
        _avatarsLoading ? 'Looking for avatars on the PC…' : "Couldn't list the PC's avatars. Is Buddy connected?",
        style: const TextStyle(color: Color(0xFF9AA3B5)),
      );
    }
    final current = avatars.contains(_c.settings.avatar) ? _c.settings.avatar : '';
    return DropdownButtonFormField<String>(
      initialValue: current,
      isExpanded: true,
      decoration: const InputDecoration(labelText: 'Avatar', border: OutlineInputBorder()),
      items: [
        const DropdownMenuItem(value: '', child: Text("The PC's default")),
        for (final name in avatars)
          DropdownMenuItem(value: name, child: Text(name.replaceFirst(RegExp(r'\.vrm$', caseSensitive: false), ''))),
      ],
      onChanged: (value) => _c.setAvatar(value ?? ''),
    );
  }

  @override
  Widget build(BuildContext context) {
    final settings = _c.settings;
    final voice = _voices.contains(settings.voice) ? settings.voice : '';
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const _Heading('Buddy on your PC'),
          ServerForm(controller: _c, onConnected: () => Navigator.of(context).pop()),
          const SizedBox(height: 28),
          const _Heading('Avatar'),
          _avatarPicker(),
          const SizedBox(height: 28),
          const _Heading('Hands-free talking'),
          SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'off', label: Text('Off')),
              ButtonSegment(value: 'vad', label: Text('Always')),
              ButtonSegment(value: 'wake', label: Text('Wake word')),
            ],
            selected: {settings.handsFree},
            showSelectedIcon: false,
            onSelectionChanged: (selection) {
              settings.handsFree = selection.first;
              _c.applyMic();
              setState(() {});
            },
          ),
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              switch (settings.handsFree) {
                'vad' => 'Buddy listens while the app is open and answers when you stop talking.',
                'wake' => 'Buddy answers when you start or end with “Hey Buddy”, and for a few seconds after it replies.',
                _ => 'Hold the mic button (or Volume Down) to talk.',
              },
              style: const TextStyle(color: Color(0xFF9AA3B5)),
            ),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Keep the mic ready'),
            subtitle: const Text(
              "Catches your first word when you hold to talk. The mic stays on while Buddy is open; "
              'nothing is sent until you talk.',
            ),
            value: settings.micReady,
            onChanged: (v) {
              settings.micReady = v;
              _c.applyMic();
              setState(() {});
            },
          ),
          const SizedBox(height: 28),
          const _Heading('Voice'),
          DropdownButtonFormField<String>(
            initialValue: voice,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Voice', border: OutlineInputBorder()),
            items: [
              const DropdownMenuItem(value: '', child: Text("Phone's default voice")),
              for (final v in _voices)
                DropdownMenuItem(
                  value: v,
                  child: Text(v.replaceFirst('|', '  ·  '), overflow: TextOverflow.ellipsis),
                ),
            ],
            onChanged: (value) {
              settings.voice = value ?? '';
              _changed();
            },
          ),
          const SizedBox(height: 16),
          _SliderRow(
            label: 'Speaking speed',
            value: settings.rate,
            min: 0.2,
            max: 1.0,
            normal: 0.5,
            onChanged: (v) {
              settings.rate = v;
              _changed();
            },
          ),
          _SliderRow(
            label: 'Voice pitch',
            value: settings.pitch,
            min: 0.5,
            max: 2.0,
            normal: 1.0,
            onChanged: (v) {
              settings.pitch = v;
              _changed();
            },
          ),
          Align(
            alignment: Alignment.centerLeft,
            child: OutlinedButton.icon(
              onPressed: _c.testVoice,
              icon: const Icon(Icons.volume_up),
              label: const Text('Test voice'),
            ),
          ),
          const SizedBox(height: 28),
          const _Heading('Gestures'),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Hold Volume Down to talk'),
            subtitle: const Text('While Buddy is open. A short press still turns the volume down.'),
            value: settings.volumeKeyTalk,
            onChanged: (v) {
              settings.volumeKeyTalk = v;
              _c.applyGestures();
              setState(() {});
            },
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Floating Buddy outside the app'),
            subtitle: Text(
              _overlayAllowed
                  ? "Buddy's head stays on screen over other apps. Drag it anywhere, tap to open, long-press to talk."
                  : 'Needs "Display over other apps". Turning this on opens that setting.',
            ),
            value: settings.floatingBubble && _overlayAllowed,
            onChanged: (v) async {
              if (v && !await _c.native.canDrawOverlays()) {
                settings.floatingBubble = true; // applied once permission is granted
                await _c.native.requestOverlayPermission();
                return;
              }
              settings.floatingBubble = v;
              await _c.applyGestures();
              setState(() {});
            },
          ),
          const Padding(
            padding: EdgeInsets.only(bottom: 8),
            child: Text('Double-tap Buddy to stop it talking.', style: TextStyle(color: Color(0xFF9AA3B5))),
          ),
          const SizedBox(height: 20),
          const _Heading('Screen'),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Keep the screen on'),
            subtitle: const Text('While Buddy is open'),
            value: settings.keepScreenOn,
            onChanged: (v) {
              settings.keepScreenOn = v;
              _c.applyKeepScreenOn();
              setState(() {});
            },
          ),
        ],
      ),
    );
  }
}

class _Heading extends StatelessWidget {
  const _Heading(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 12),
    child: Text(text, style: Theme.of(context).textTheme.titleMedium),
  );
}

class _SliderRow extends StatelessWidget {
  const _SliderRow({
    required this.label,
    required this.value,
    required this.min,
    required this.max,
    required this.normal,
    required this.onChanged,
  });

  final String label;
  final double value, min, max, normal;
  final ValueChanged<double> onChanged;

  @override
  Widget build(BuildContext context) {
    final relative = value / normal;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text(label),
            const Spacer(),
            Text('${relative.toStringAsFixed(2)}×', style: const TextStyle(color: Color(0xFF9AA3B5))),
            TextButton(onPressed: () => onChanged(normal), child: const Text('Normal')),
          ],
        ),
        Slider(value: value.clamp(min, max), min: min, max: max, onChanged: onChanged),
      ],
    );
  }
}
