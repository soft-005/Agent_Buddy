import 'package:flutter/material.dart';

import '../buddy_client.dart';
import '../controller.dart';
import 'approval_card.dart';
import 'avatar_view.dart';
import 'loader_card.dart';
import 'server_form.dart';
import 'settings_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.controller});

  final BuddyController controller;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver {
  final _input = TextEditingController();

  BuddyController get _c => widget.controller;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _input.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) _c.stopTalking(); // don't keep the mic on in the background
    // Hands-free listening pauses while the app is out of sight. (Not on "inactive":
    // that also happens for a moment when a permission dialog opens.)
    if (state == AppLifecycleState.resumed) _c.setForeground(true);
    if (state == AppLifecycleState.hidden || state == AppLifecycleState.paused) _c.setForeground(false);
  }

  void _send() {
    _c.sendText(_input.text);
    _input.clear();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Color(0xFF1B2140), Color(0xFF0E1020)],
          ),
        ),
        child: SafeArea(
          child: ListenableBuilder(
            listenable: _c,
            builder: (context, _) => Column(
              children: [
                _TopBar(controller: _c),
                Expanded(
                  child: Stack(
                    children: [
                      Positioned.fill(
                        // Double-tap the avatar to stop Buddy talking.
                        child: GestureDetector(
                          behavior: HitTestBehavior.opaque,
                          onDoubleTap: _c.stopBuddy,
                          child: AvatarView(controller: _c),
                        ),
                      ),
                      if (!_c.needsSetup)
                        Positioned(left: 12, right: 12, bottom: 8, child: _ChatList(messages: _c.messages)),
                      if (_c.loaderVisible && !_c.needsSetup)
                        Positioned(left: 12, right: 12, top: 4, child: LoaderCard(controller: _c)),
                      if (_c.approval != null && !_c.needsSetup)
                        Positioned(
                          left: 12,
                          right: 12,
                          bottom: 8,
                          child: ApprovalCard(key: ValueKey(_c.approval!.id), controller: _c, approval: _c.approval!),
                        ),
                      if (_c.needsSetup) Center(child: _SetupCard(controller: _c)),
                    ],
                  ),
                ),
                if (!_c.needsSetup) _InputBar(controller: _c, input: _input, onSend: _send),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _TopBar extends StatelessWidget {
  const _TopBar({required this.controller});

  final BuddyController controller;

  @override
  Widget build(BuildContext context) {
    final (color, label) = switch (controller.client.connection.value) {
      Connection.connected => (const Color(0xFF30A46C), 'Connected'),
      Connection.connecting => (const Color(0xFFF5A524), 'Connecting'),
      Connection.disconnected => (const Color(0xFFE5484D), 'Offline'),
    };
    return Padding(
      padding: const EdgeInsets.fromLTRB(18, 6, 6, 0),
      child: Row(
        children: [
          const Text('Buddy', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
          const SizedBox(width: 12),
          Container(
            width: 9,
            height: 9,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 6),
          Text(label, style: const TextStyle(color: Color(0xFF9AA3B5), fontSize: 13)),
          const Spacer(),
          IconButton(
            tooltip: 'New conversation',
            icon: const Icon(Icons.add_comment_outlined),
            onPressed: () => _confirmNewConversation(context),
          ),
          IconButton(
            tooltip: 'Settings',
            icon: const Icon(Icons.settings_outlined),
            onPressed: () =>
                Navigator.of(context).push(MaterialPageRoute(builder: (_) => SettingsScreen(controller: controller))),
          ),
        ],
      ),
    );
  }

  Future<void> _confirmNewConversation(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Start a new conversation?'),
        content: const Text(
          "Buddy forgets this chat, but keeps what's in its memory. "
          'The old conversation stays on the PC in backend/data/conversations.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.of(context).pop(true), child: const Text('New conversation')),
        ],
      ),
    );
    if (ok == true) controller.newConversation();
  }
}

class _ChatList extends StatelessWidget {
  const _ChatList({required this.messages});

  final List<ChatMessage> messages;

  @override
  Widget build(BuildContext context) {
    final recent = messages.length > 30 ? messages.sublist(messages.length - 30) : messages;
    return ConstrainedBox(
      constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.32),
      child: ListView.builder(
        reverse: true, // newest at the bottom, list grows upward
        shrinkWrap: true,
        itemCount: recent.length,
        itemBuilder: (context, i) => _Bubble(recent[recent.length - 1 - i]),
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble(this.message);

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final ok = message.toolOk;
    if (ok != null) {
      // An action Buddy took on the PC: a small line, not a bubble.
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 3, horizontal: 6),
        child: Row(
          children: [
            Icon(
              ok ? Icons.check_circle : Icons.cancel,
              size: 15,
              color: ok ? const Color(0xFF30A46C) : const Color(0xFFE5484D),
            ),
            const SizedBox(width: 6),
            Flexible(
              child: Text(
                ok ? message.text : '${message.text} (not done)',
                style: const TextStyle(color: Color(0xFFB9C2D3), fontSize: 13),
              ),
            ),
          ],
        ),
      );
    }
    final user = message.fromUser;
    final background = message.isError
        ? const Color(0xE65A1E22)
        : user
        ? const Color(0xE63A5BD9)
        : const Color(0xEEF4F6FB);
    final foreground = message.isError ? const Color(0xFFFFD5D7) : (user ? Colors.white : const Color(0xFF1D1D1F));
    return Align(
      alignment: user ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 3),
        padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 9),
        constraints: BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * 0.78),
        decoration: BoxDecoration(color: background, borderRadius: BorderRadius.circular(16)),
        child: Text(message.text.isEmpty ? '…' : message.text, style: TextStyle(color: foreground, fontSize: 15)),
      ),
    );
  }
}

class _InputBar extends StatelessWidget {
  const _InputBar({required this.controller, required this.input, required this.onSend});

  final BuddyController controller;
  final TextEditingController input;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    final listening = controller.listening || controller.heardSpeech;
    final hint = listening
        ? 'Listening…'
        : switch (controller.settings.handsFree) {
            'vad' => 'Just talk, or type…',
            'wake' => 'Say “Hey Buddy”, or type…',
            _ => 'Type to Buddy…',
          };
    const green = Color(0xFF30A46C);
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 12),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: input,
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => onSend(),
              decoration: InputDecoration(
                hintText: hint,
                filled: true,
                fillColor: const Color(0x22FFFFFF),
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: BorderSide.none),
                suffixIcon: IconButton(icon: const Icon(Icons.send_rounded), onPressed: onSend),
              ),
            ),
          ),
          const SizedBox(width: 10),
          // Hold to talk: recording runs while the finger is down. With hands-free
          // on, the button has a green ring that glows while someone is heard.
          Listener(
            onPointerDown: (_) => controller.startTalking(),
            onPointerUp: (_) => controller.stopTalking(),
            onPointerCancel: (_) => controller.stopTalking(),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 150),
              width: listening ? 66 : 58,
              height: listening ? 66 : 58,
              decoration: BoxDecoration(
                color: listening ? const Color(0xFFE5484D) : const Color(0xFF4A7DFF),
                shape: BoxShape.circle,
                border: controller.handsFreeOn ? Border.all(color: green, width: 3) : null,
                boxShadow: [
                  BoxShadow(
                    color: controller.hearing
                        ? green.withValues(alpha: 0.7)
                        : (listening ? const Color(0xFFE5484D) : const Color(0xFF4A7DFF)).withValues(alpha: 0.45),
                    blurRadius: listening || controller.hearing ? 22 : 10,
                  ),
                ],
              ),
              child: Icon(listening ? Icons.graphic_eq : Icons.mic, color: Colors.white, size: 28),
            ),
          ),
        ],
      ),
    );
  }
}

class _SetupCard extends StatelessWidget {
  const _SetupCard({required this.controller});

  final BuddyController controller;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.all(20),
      color: const Color(0xF0181A22),
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('Connect to Buddy', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            const Text(
              'Buddy runs on your PC. On the PC, set LAN_ACCESS=true in backend/.env and start Buddy. '
              'Then find it on your Wi-Fi or type its address.',
              style: TextStyle(color: Color(0xFFB9C2D3)),
            ),
            const SizedBox(height: 16),
            ServerForm(controller: controller),
          ],
        ),
      ),
    );
  }
}
