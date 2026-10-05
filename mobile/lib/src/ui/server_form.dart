import 'package:flutter/material.dart';

import '../controller.dart';
import '../discovery.dart';
import '../settings.dart';

/// The PC address field with "Find on Wi-Fi" and "Connect".
class ServerForm extends StatefulWidget {
  const ServerForm({super.key, required this.controller, this.onConnected});

  final BuddyController controller;
  final VoidCallback? onConnected;

  @override
  State<ServerForm> createState() => _ServerFormState();
}

class _ServerFormState extends State<ServerForm> {
  late final TextEditingController _field = TextEditingController(text: widget.controller.settings.server);
  bool _searching = false;
  String _message = '';

  @override
  void dispose() {
    _field.dispose();
    super.dispose();
  }

  Future<void> _find() async {
    setState(() {
      _searching = true;
      _message = 'Looking for Buddy on this Wi-Fi…';
    });
    // A port typed in the field ("192.168.1.134:9000" or ":9000") is searched instead of 8818.
    final typed = RegExp(r':(\d+)\s*$').firstMatch(_field.text)?.group(1);
    final found = await findBuddy(port: int.tryParse(typed ?? '') ?? defaultPort);
    if (!mounted) return;
    setState(() {
      _searching = false;
      if (found.isEmpty) {
        _message = "Couldn't find Buddy. Is the PC on the same Wi-Fi, with LAN_ACCESS=true in backend/.env?";
      } else {
        _field.text = found.first;
        _message = found.length == 1 ? 'Found Buddy at ${found.first}.' : 'Found: ${found.join(', ')}';
      }
    });
  }

  void _connect() {
    if (webSocketUri(_field.text) == null) {
      setState(() => _message = 'Enter the PC address, for example 192.168.1.134');
      return;
    }
    widget.controller.settings.server = _field.text;
    widget.controller.connect();
    widget.onConnected?.call();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        TextField(
          controller: _field,
          keyboardType: TextInputType.url,
          autocorrect: false,
          decoration: const InputDecoration(
            labelText: "PC address",
            hintText: '192.168.1.134',
            helperText: "The PC running Buddy. Add :port if it isn't 8818 (PORT in backend/.env).",
            border: OutlineInputBorder(),
          ),
          onSubmitted: (_) => _connect(),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                onPressed: _searching ? null : _find,
                icon: _searching
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.wifi_find),
                label: const Text('Find on Wi-Fi'),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: FilledButton.icon(onPressed: _connect, icon: const Icon(Icons.link), label: const Text('Connect')),
            ),
          ],
        ),
        if (_message.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 10),
            child: Text(_message, style: Theme.of(context).textTheme.bodySmall),
          ),
      ],
    );
  }
}
